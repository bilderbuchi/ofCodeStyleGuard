"""Automatic mechanism to make sure code style of PRs is checked."""

import logging
import json
import threading
import os
import sys
import subprocess
import shlex
import github
import Queue
import errno
import shutil
from requests import Session, get
from time import sleep
from styleguard_config import cfg
from stat import S_IEXEC
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# TODO: Wishlist: comment-style PR feedback


class LessThanLevelFilter(logging.Filter):  # pylint: disable=R0903
	"""Custom logging filter which only passes events below passlevel"""
	def __init__(self, passlevel):
		# TODO: workaround: In python 2.6, logging.Filter is old-style class
		# super(LessThanLevelFilter, self).__init__()
		logging.Filter.__init__(self)
		self.passlevel = passlevel

	def filter(self, record):
		return (record.levelno < self.passlevel)


LOGGER = logging.getLogger('styleguard')
MY_FORMAT = "%(levelname)s\t%(message)s"
#Warning and above goes to stderr
eh = logging.StreamHandler(sys.stderr)  # pylint: disable=C0103
eh.setFormatter(logging.Formatter(MY_FORMAT))
eh.setLevel(logging.WARNING)
# everything from Debug to Info goes to stdout
sh = logging.StreamHandler(sys.stdout)  # pylint: disable=C0103
sh.setFormatter(logging.Formatter(MY_FORMAT))
sh.setLevel(logging.DEBUG)
sh.addFilter(LessThanLevelFilter(logging.WARNING))
LOGGER.addHandler(eh)
LOGGER.addHandler(sh)
LOGGER.setLevel(cfg['logging_level'])

logging.getLogger('github.Requester').setLevel(logging.INFO)
MY_QUEUE = Queue.Queue()
MY_DICT = dict(TOKEN='', OWNER_REPO='')


class PrHandler(threading.Thread):
	"""Threaded PR Worker"""

	def __init__(self):
		LOGGER.debug("Starting PR worker thread")
		threading.Thread.__init__(self)
		self.queue = MY_QUEUE
		self.payload = None
		self.reporoot = os.getenv('OPENSHIFT_REPO_DIR', '')
		# base directory:
		self.basedir = os.path.abspath(os.path.join(os.getcwd(),
													cfg['storage_dir']))
		self.repodir = os.path.join(self.basedir, cfg['repo_local_path'])
		self.stylerdir = os.path.join(self.basedir, cfg['styler_local_path'])
		if not os.path.exists(self.repodir):
			os.mkdir(self.repodir)
		if not os.path.exists(self.stylerdir):
			os.mkdir(self.stylerdir)
		LOGGER.debug('PATH: ' + os.getenv('PATH', 'unset'))

		self.api_github = self.init_authentication()
		if self.api_github == 1:
			raise PRHandlerException('Initialization failed. Aborting.')
		LOGGER.debug('Remaining Github API calls: ' +
					str(self.api_github.rate_limiting[0]))
		if cfg['fetch_method'] == 'git':
			if os.path.isdir(os.path.join(self.repodir, '.git')):
				LOGGER.info('Local git repo at ' + str(self.repodir))
			else:
				raise PRHandlerException('Not a git repo directory: ' +
										str(self.repodir))
			LOGGER.info('Checking repo')
			if git_command('status --porcelain', self.repodir, True, False):
				raise PRHandlerException('Local git repo is dirty!' +
										' Correct this first!')
		MY_DICT['OWNER_REPO'] = (cfg['repo_git_url']
										.rstrip('.git').split('github.com/')[1])
		self.daemon = True
		self.start()

	def run(self):
		while True:
			LOGGER.info("Waiting in worker run()")
#			LOGGER.debug('run self.queue id: ' + str(id(self.queue)))
			self.payload = self.queue.get()
			LOGGER.info(60 * '*')
			LOGGER.info("Aquired new payload: PR " + str(self.payload["number"]))
			LOGGER.info('UTC time: ' + str(datetime.utcnow()))
			if self.validate_pr():
				try:
					filtered_files = self.get_pr()
					result = self.check_style(filtered_files)
					my_gist = None
					if result['patch_file_name']:  # There's a patch file
						my_gist = self.create_gist(result)
					if not cfg['suppress_feedback']:
						self.publish_results(result, my_gist)
				except PRHandlerException as exc:
					LOGGER.error('An error occured in the PR handler:' + str(exc))
				finally:
					# guarantee that clean up runs even if exceptions occur
					self.clean_up()
			else:
				LOGGER.warning('Skipping PR ' + str(self.payload["number"]))
			LOGGER.debug('Remaining Github API calls: ' +
						str(self.api_github.rate_limiting[0]))
			self.queue.task_done()
			LOGGER.info("Finished processing payload PR " + str(self.payload["number"]))
			LOGGER.debug("self.queue size: " + str(self.queue.qsize()))

	def init_authentication(self):
		"""Create appropriate Github API user"""
		LOGGER.info('Creating API-authenticated user')
		with open(os.path.join(self.basedir, cfg['authfile']), 'r') as authfile:
			auths_temp = json.load(authfile)
		if cfg['feedback_method'] == "status":
			if all(scope in auths_temp['ofbot_codestyle_status']['scopes']
					for scope in ['repo:status', 'gist']):
				# Return authorized PyGithub Github API instance
				gh_instance = github.Github(auths_temp['ofbot_codestyle_status']['token'])
				# Verification of authentication
				try:
					_unused_var = gh_instance.get_user().name
				except github.GithubException as exception:
					# will throw 401 {u'message': u'Bad credentials'}
					LOGGER.critical('Authentication invalid: ' +
								str(exception.status) + " " + str(exception.data))
					return 1
				MY_DICT['TOKEN'] = auths_temp['ofbot_codestyle_status']['token']
				return gh_instance
			else:
				LOGGER.error('Could not authenticate for Status API' +
							' with auth ofbot_codestyle_status')
				return 1
		elif cfg['feedback_method'] == "comment":
			raise PRHandlerException('Comment auth not yet implemented!')
#			Wishlist: comment-style PR feedback
#			return 1
		else:
			LOGGER.error("Unknown feedback method: " +
							str(cfg['feedback_method']))
			return 1

	def validate_pr(self):
		"""Determine if the current PR is valid for processing"""
		LOGGER.info('Verifying information from payload')
		if self.payload['base']['repo']['git_url'] == cfg['repo_git_url']:
			verified = True
		else:
			verified = False
			LOGGER.warning('PR git_url ' +
							self.payload['base']['repo']['git_url'] +
							' does not match config: ' + cfg['repo_git_url'])
		if self.payload['state'] == 'open':
			verified = verified and True
		else:
			verified = False
			LOGGER.warning('PR is not open!')
		if self.payload['merged'] == False:
			verified = verified and True
		else:
			verified = False
			LOGGER.warning('PR is already merged!')

		# Mergeability checking is asynchronous on Github, so this has to be
		# confirmed after receipt of the PR webhook
		if verified:
			LOGGER.info("Checking if PR is mergeable")
			sleep(5)
			if (self.api_github.get_repo(self.payload['base']['repo']['full_name'])
								.get_pull(self.payload['number'])
								.mergeable == True):
				mergeable = True
			else:
				mergeable = False
				LOGGER.warning('PR is not mergeable. Not styling files.')
				if not cfg['suppress_feedback']:
					self.add_status('pending',
							'Code style check postponed until PR is mergeable')
			verified = verified and mergeable

		if not verified:
			LOGGER.warning('PR ' +
							str(self.payload["number"]) +
							' is not valid.')
		else:
			LOGGER.info('PR ' + str(self.payload["number"]) +
						' is valid.')
		return verified

	def get_pr(self):
		"""Get PR and styler files

		Return list of files to be styled"""
		LOGGER.info('Getting PR and styler files')
		LOGGER.debug('Generating Github API objects')
		api_repo = (self.api_github.
					get_repo(self.payload['base']['repo']['full_name']))
		api_pr = api_repo.get_pull(self.payload['number'])

		if cfg['fetch_method'] == 'git':
			changed_files = self.git_process_pr()
			filtered_file_list = self.filter_file_list(changed_files)
		elif cfg['fetch_method'] == 'file':
			changed_files, filtered_file_list = self.file_process_pr(api_pr)

		styler_files = ['scripts/dev/style/ofStyler',
				'scripts/dev/style/openFrameworks_style.cfg',
				'scripts/dev/style/core_header.txt']
		if any(stylefile in changed_files for stylefile in styler_files):
			# Styler files have been updated in the PR, use those.
			source = 'pr'
			LOGGER.info('Getting styler from PR')
		else:
			# Styler files in base branch are the most current
			source = 'base'
			LOGGER.info('Getting styler from base branch')
		self._fetch_styler_files(api_repo, api_pr, styler_files, source)

		return filtered_file_list

	def git_process_pr(self):
		"""Process the PR using the git repo.

		Return the list of files added or modified in the PR"""
		LOGGER.info('Starting git processing of PR')
		git_command('checkout master', self.repodir)
		git_command('submodule update --init', self.repodir)

		# Identify base remote, and fetch updates
		base_remote = None
		remotes_string = git_command('remote -v', self.repodir, True)
		my_remotes = [x.split() for x in remotes_string.split('\n')]
		# my_remotes[remotes][name-url-(fetch/push)]
		for rem in my_remotes:
			if rem[2] == "(fetch)":
				LOGGER.debug('Found remote ' + rem[0] + ': ' + rem[1])
				if (rem[1] == self.payload['base']['repo']['git_url']
				or rem[1] == self.payload['base']['repo']['ssh_url']):
					base_remote = rem[0]
					LOGGER.info('Base remote: ' + base_remote)
		if base_remote is None:
			raise PRHandlerException('Base remote does not exist yet, with URL ' +
							self.payload['base']['repo']['git_url'] +
							' Please create it first in the local git repo.')

		# update repo and check out base branch
		LOGGER.info('Getting the base branch')
		git_command('fetch ' + base_remote, self.repodir)
		base_branch_name = self.payload['base']['ref']
		LOGGER.debug('Base branch name: ' + base_branch_name)

		if not git_command('show-ref --verify --heads --quiet -- refs/heads/' +
				base_branch_name, self.repodir):
			# branch already exists, check it out
			git_command('checkout ' + base_branch_name, self.repodir)
			git_command('merge ' + base_remote + '/' +
					base_branch_name, self.repodir)
		else:
			# branch does not exist locally yet, get it
			git_command('checkout -b ' + base_branch_name + ' ' +
					base_remote + '/' + base_branch_name, self.repodir)
		git_command('submodule update --init', self.repodir)
		LOGGER.info('Base branch at: ' +
					git_command('log --pretty=format:"%h - %s" -n 1 HEAD',
								self.repodir, True, False))

		LOGGER.info('Getting the PR branch')
		pr_number = self.payload['number']
		pr_branch_name = 'pr-' + str(pr_number)
		git_command('fetch ' + base_remote + ' pull/' + str(pr_number) +
					'/head:' + pr_branch_name, self.repodir)
		git_command('checkout ' + pr_branch_name, self.repodir)
		git_command('submodule update --init', self.repodir)
		LOGGER.info('PR branch at: ' +
			git_command('log --pretty=format:"%h - %s" -n 1 HEAD',
						self.repodir, True, False))

		LOGGER.info('Determine files added/modified in the PR')
		changed_files = str(git_command('diff --name-only --diff-filter=AM ' +
									base_branch_name + '...' +
									pr_branch_name, self.repodir, True, False))

		# after this, we have a clean git repo with the PR branch checked out
		return changed_files.split()

	def file_process_pr(self, api_pr):
		"""Process the PR using manually fetched files.
		This has the advantage that the disk space requirements are lower.

		Return the list of files added or modified in the PR
		"""
		LOGGER.info('Starting processing of PR with fetched files')
		LOGGER.info('Generating list of changed files')

		changed_files = []
		filtered_files = []

		LOGGER.info('Fetching PR files. This will take a while.')
		session = Session()

		for tmp_f in api_pr.get_files():
			if (tmp_f.status in ['modified', 'added']):
				changed_files.append(tmp_f.filename)
				if self.filter_file_list([tmp_f.filename]):
					filtered_files.append(tmp_f.filename)  # full path from repo root
					LOGGER.debug('Fetching ' + tmp_f.filename)
					resp = session.get(tmp_f.raw_url)
					destination = os.path.join(self.repodir, tmp_f.filename)
					try:
						os.makedirs(os.path.dirname(destination))
					except os.error as exc:
						if exc.errno != errno.EEXIST:
							raise
					with open(destination, 'wb') as store_file:
						store_file.write(resp.content)  # pylint: disable=E1103

		LOGGER.info('Creating temporary git repository')
		git_command('init', self.repodir)
		git_command('config core.autocrlf input', self.repodir)
		git_command('config core.filemode false', self.repodir)
		git_command('add .', self.repodir)
		git_command('commit -qam "PR commit"', self.repodir)

		# we end up with a clean small git repo containing the PR files
		return changed_files, filtered_files

	def _fetch_styler_files(self, api_repo, api_pr, styler_files, source):
		"""Fetch the appropriate styler files.

		Use either the PR HEAD or base branch HEAD"""
		LOGGER.info('Fetching styler files')
		if source == 'base':
			source_commit = api_pr.base.sha
		elif source == 'pr':
			source_commit = api_pr.head.sha
		else:
			raise PRHandlerException('Unknown source: ' + source)

		# temporary workaround
		for styler_file in styler_files:
			LOGGER.debug('Fetching ' + styler_file)
			# ATTENTION: For simplicity, any directories are stripped from styler_file!
			destination = os.path.join(self.stylerdir, os.path.basename(styler_file))
			try:
				os.makedirs(os.path.dirname(destination))
			except os.error as exc:
				if exc.errno != errno.EEXIST:
					raise
			with open(destination, 'wb') as filehandle:
				content = api_repo.get_contents(styler_file, source_commit).content
				encoding = api_repo.get_contents(styler_file, source_commit).encoding
				filehandle.write(content.decode(encoding))
			if styler_file.endswith('ofStyler'):
				os.chmod(destination, os.stat(destination).st_mode | S_IEXEC)

#		pr_repo.get_contents(path, source_commit)

	@staticmethod
	def filter_file_list(file_list):
		"""Filter a list of file paths according to defined criteria.

		Return filtered list"""
		# Only check files touched by the PR which are also in the desired fileset:
		# `cpp` and `h` files in official `addons`, `examples`, `devApps`,
		# `libs/openFrameworks`)
#		LOGGER.info('Filtering file list')
		dummy_list = [filename for filename in file_list if
			filename.lower().endswith(('.cpp', '.h')) and
			filename.lower().startswith(('examples',
										'addons',
										'apps',
										'libs' + os.path.sep + 'openframeworks'))]
		return dummy_list

	def check_style(self, file_list):
		"""Check style of the given list of files"""
		LOGGER.info('Checking style of changed/added files')

		LOGGER.info('Styling files')
		for tmp_file in file_list:
			style_file(os.path.abspath(os.path.join(self.repodir, tmp_file)),
						self.stylerdir)
		LOGGER.info('Finished styling. Checking if there were changes.')
		pr_number = self.payload['number']
		pr_url = self.payload['html_url']

		# check if styling changed any files
		if git_command('status --porcelain', self.repodir, True, False):
			patch_file_name = ('pr-' + str(pr_number) + '.patch')
			LOGGER.info('Changes detected. Creating patch file ' + patch_file_name)
			with open(os.path.join(self.basedir, 'patches',
									patch_file_name), 'w') as patchfile:
				# OK to use git diff since only text files will be modified
				patchfile.write(git_command('diff HEAD', self.repodir, True, False))
			# test if patch applies cleanly
			git_command('reset --hard HEAD', self.repodir)
			if git_command('apply --index --check ' +
						os.path.join(self.basedir, 'patches', patch_file_name),
						self.repodir, True):
				raise PRHandlerException('Patch' + patch_file_name +
								' does not apply cleanly, aborting!')
			else:
				LOGGER.info('Patch ' + patch_file_name + ' applies cleanly')
		else:
			patch_file_name = ''
			LOGGER.info("PR already conforms to style")

		# now, reset HEAD so that the repo is clean again
		LOGGER.debug('Resetting HEAD to get a clean repo')
		git_command('reset --hard HEAD', self.repodir)
		git_command('submodule update --init', self.repodir)

		return {'pr_number': pr_number,
				'pr_url': pr_url,
				'patch_file_name': patch_file_name}

	def publish_results(self, result, gist):
		"""Report back to PR, either via Github Status API or ofbot comments"""
		if cfg['feedback_method'] is "status":
			if result['patch_file_name'] and gist:
				self.add_status(state='failure',
						target_url=gist.html_url,
						description='PR does not conform to style. Click for details.')
			else:
				# no patch necessary-> green status
				self.add_status(state='success',
								description='PR conforms to code style.')
		elif cfg['feedback_method'] is "comment":
			raise PRHandlerException('Comment feedback not yet implemented.' +
									' Aborting.')
#			self.add_comment(result, gist)
		else:
			raise PRHandlerException("Unknown feedback method: " +
							str(cfg['feedback_method']))

	def add_status(self, state, description, target_url=None):
		"""Add the relevant codestyle information via a PR Status"""
		LOGGER.info('Adding ' + state + ' Status to PR')
		repo = self.api_github.get_repo(self.payload['base']['repo']['full_name'])
		commit = repo.get_commit(self.payload['head']['sha'])
		# State: success, failure, error, or pending
		if not state in ['success', 'failure', 'error', 'pending']:
			raise PRHandlerException('Status state ' + state + 'is invalid!')
		if target_url:
			commit.create_status(state=state,
							description=description,
							target_url=target_url)
		else:
			commit.create_status(state=state, description=description)
#
#	def add_comment(self, result, gist):
#		"""Add the relevant codestyle information via a comment on the thread"""
#		raise PRHandlerException('Feedback via comments not yet implemented.' +
#									' Aborting.')

	def create_gist(self, result):
		"""Create gist with usage instructions and patch file.

		Return Gist object for further consumption"""
		LOGGER.info('Creating gist')
		with open(os.path.join(self.reporoot,
							'gist_description.md'), 'r') as descfile:
			desc_string = descfile.read().format(result['pr_number'], result['pr_url'])
			with open(os.path.join(self.basedir, 'patches', result['patch_file_name']),
					'r') as patchfile:
				patch_file_name = 'pr-' + str(result['pr_number']) + '.patch'
				desc_file_name = ('OF_PR' + str(result['pr_number']) + '-' +
					self.payload['head']['sha'][1:7] + '.md')
				my_gist = self.api_github.get_user().create_gist(True,
					{desc_file_name: github.InputFileContent(desc_string),
					patch_file_name: github.InputFileContent(patchfile.read())},
					'OF Code style patch for PR ' + str(result['pr_number']))
		LOGGER.info('Created Gist ' + my_gist.html_url)
		return my_gist

	def clean_up(self):
		"""Clean up the repo"""
		LOGGER.info('Cleaning up.')
		if cfg['fetch_method'] == 'git':
			git_command('checkout master', self.repodir)
			git_command('submodule update --init', self.repodir)
		elif cfg['fetch_method'] == 'file':
			shutil.rmtree(self.repodir)
			os.mkdir(self.repodir)
		shutil.rmtree(self.stylerdir)
		os.mkdir(self.stylerdir)


class PRHandlerException(Exception):
	""" Self-defined Exception for error handling"""
	pass


def git_command(arg_string, repo_dir, return_output=False, log_output=True):
	"""Execute git command in repo_dir and log output to LOGGER"""
	try:
		# TODO: remove this workaround when this bug in OpenShift is fixed:
		# https://bugzilla.redhat.com/show_bug.cgi?id=912748
		# If the GIT_DIR environment variable exists, unset it during execution
		if os.getenv('GIT_DIR') and os.getenv('OPENSHIFT_APP_NAME'):
			cmd_prefix = '/bin/env -u GIT_DIR '
		else:
			cmd_prefix = ''
		# the argument string has to be split if Shell==False in check_output
		output = subprocess.check_output(shlex.split(cmd_prefix + 'git ' +
													arg_string),
										stderr=subprocess.STDOUT, cwd=repo_dir)
		if output and log_output:
			LOGGER.debug(str(output).rstrip('\n'))
		if return_output:
			return str(output)
	except subprocess.CalledProcessError as exc:
		if log_output:
			LOGGER.error(str(exc.cmd) + ' failed with exit status ' +
						str(exc.returncode) + ':')
			if hasattr(exc, 'output'):
				LOGGER.error(exc.output)
			else:
				LOGGER.error(str(exc))
		if return_output:
			if hasattr(exc, 'output'):
				return exc.output
			else:
				return str(exc)


def style_file(my_file, style_tool_dir):
	""" Call style tool on file and log output to LOGGER"""
	try:
		# the argument string has to be split if Shell==False in check_output
		# shlex seems to to bad things here, encode('ascii') as a workaround
		# execv() argument 1 must be encoded string without NULL bytes, not str
		output = subprocess.check_output(shlex.split(('.' + os.path.sep +
													'ofStyler ' + my_file).encode('ascii')),
										stderr=subprocess.STDOUT, cwd=style_tool_dir)
		if output:
			LOGGER.debug(str(output).rstrip('\n'))
	except subprocess.CalledProcessError as exc:
		LOGGER.error(str(exc.cmd) + ' failed with exit status ' +
					str(exc.returncode) + ':')
		if hasattr(exc, 'output'):
			LOGGER.error(exc.output)
		else:
			LOGGER.error(str(exc))


def handle_payload(payload):
	"""	Queue new PRs coming in during processing"""
	if type(payload) == int:
		parameters = {'access_token': MY_DICT['TOKEN']}
		# GET /repos/:owner/:repo/pulls/:number
		url = ('https://api.github.com/repos/' + MY_DICT['OWNER_REPO'] +
				'/pulls/' + str(payload))
		req = get(url, params=parameters)
		if not req.ok:
			LOGGER.error('An error occured getting the PR payload data: ' +
						req.text)
		else:
			payload = req.json()
			LOGGER.debug("handing payload off to queue")
			MY_QUEUE.put(payload)
	elif type(payload) == dict:
		LOGGER.info('Received PR ' + str(payload['number']) + ': ' +
					payload['title'])
		basedir = os.path.abspath(os.path.join(os.getcwd(), cfg['storage_dir']))
		with open(os.path.join(basedir, 'last_payload.json'), 'w') as outfile:
			json.dump(payload, outfile, indent=2)
		LOGGER.debug("handing payload off to queue")
		MY_QUEUE.put(payload)
	else:
		LOGGER.error('Unknown type of payload: ' + str(type(payload)))


def add_file_logger():
	"""Enable logging to file"""
	if 'logfile' in cfg and cfg['logfile']:
		logdir = os.path.abspath(os.path.join(os.getcwd(),
											cfg['storage_dir'], 'logs'))
		if not os.path.exists(logdir):
			os.mkdir(logdir)
		logfile = os.path.join(logdir, cfg['logfile'])
		# Add file logger. Rotate every midnight, keep 14 days of files
		filehandler = TimedRotatingFileHandler(logfile, when='midnight',
			backupCount=14, utc=True)
		filehandler.setFormatter(logging.Formatter(MY_FORMAT))
		root_logger = logging.getLogger()
		root_logger.addHandler(filehandler)
