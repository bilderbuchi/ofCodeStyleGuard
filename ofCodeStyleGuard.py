#!/usr/bin/python
"""Automatic mechanism to make sure code style of PRs is checked."""
import web
import logging
import json
import threading
import sys
import os
import subprocess
import shlex
import github
from time import sleep
from styleguard_module import my_config, my_queue

# TODO: Implement manual triggering of PR checks
# TODO: web.py -> flask/Werkzeug

LOGGER = logging.getLogger(' ')
logging.basicConfig(level=my_config['logging_level'])
if my_config['logging_level'] == logging.DEBUG:
	os.environ['GIT_PYTHON_TRACE'] = "full"  # '1' or 'full' for including output
else:
	os.environ['GIT_PYTHON_TRACE'] = "0"
URLS = (
	'/', 'My_endpoint'
)


class PrHandler(threading.Thread):  # pylint: disable=R0902
	"""Threaded PR Worker"""

	def __init__(self, queue):
		LOGGER.debug("Starting PR worker thread")
		threading.Thread.__init__(self)
		self.queue = queue
		self.payload = None
		self.basedir = os.getcwd()  # base directory
		self.repodir = os.path.abspath(os.path.join(self.basedir,
													my_config['repo_local_path']))
		self.stylerdir = os.path.abspath(os.path.join(self.basedir,
													my_config['style_tool_path']))

		self.api_github = self.init_authentication()
		if self.api_github == 1:
			LOGGER.critical('Initialization failed. Aborting.')
			sys.exit()
		if os.path.isdir(os.path.join(self.repodir, '.git')):
			LOGGER.info('Local git repo at ' + str(self.repodir))
		else:
			LOGGER.critical('Not a git repo directory: ' + str(self.repodir))
			sys.exit()

		LOGGER.info('Checking repo')
		if git_command('status --porcelain', self.repodir, True, False):
			LOGGER.critical('Local git repo is dirty! Correct this first!')
			sys.exit()

	def run(self):
		while True:
			LOGGER.info("Waiting in worker run()")
#			LOGGER.debug('run self.queue id: ' + str(id(self.queue)))
			self.payload = self.queue.get()
			LOGGER.info("Aquired new payload: PR " + str(self.payload["number"]))
			if self.validate_pr():
				try:
					changed_files = self.git_process_pr()
					result = self.check_style(changed_files)
					self.publish_results(result)
				except PRHandlerException as exc:
					LOGGER.error('An error occured in the PR handler:' + str(exc))
				finally:
					# guarantee that clean up runs even if exceptions occur
					self.clean_up()
			else:
				LOGGER.warning('Skipping PR ' + str(self.payload["number"]))
			self.queue.task_done()
			LOGGER.info("Finished processing payload PR " + str(self.payload["number"]))
			LOGGER.debug("self.queue size: " + str(self.queue.qsize()))

	@staticmethod
	def init_authentication():
		"""Create appropriate Github API user"""
		LOGGER.info('Creating API-authenticated user')
		with open(my_config['authfile'], 'r') as authfile:
			auths_temp = json.load(authfile)
		if my_config['feedback_method'] == "status":
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
				return gh_instance
			else:
				LOGGER.error('Could not authenticate for Status API' +
							' with auth ofbot_codestyle_status')
				return 1
		elif my_config['feedback_method'] == "comment":
			LOGGER.critical('Comment authorization not yet implemented!')
#			TODO: implement comment-style PR feedback
			return 1
		else:
			LOGGER.error("Unknown feedback method: " +
							str(my_config['feedback_method']))
			return 1

	def validate_pr(self):
		"""Determine if the current PR is valid for processing"""
		LOGGER.info('Verifying information from payload')
		if self.payload['repository']['git_url'] == my_config['repo_git_url']:
			verified = True
		else:
			verified = False
			LOGGER.warning('PR git_url ' +
							self.payload['repository']['git_url'] +
							' does not match config: ' + my_config['repo_git_url'])
		if self.payload['action'] != 'closed':
			verified = verified and True
		else:
			verified = False
			LOGGER.warning('PR is closed!')
		if self.payload['pull_request']['merged'] == False:
			verified = verified and True
		else:
			verified = False
			LOGGER.warning('PR is already merged!')

		# Mergeability checking is asynchronous on Github, so this has to be
		# confirmed after receipt of the PR
		LOGGER.info("Checking if PR is mergeable")
		sleep(5)
		if (self.api_github.get_repo(self.payload['repository']['full_name'])
							.get_pull(self.payload['pull_request']['number'])
							.mergeable == True):
			mergeable = True
		else:
			mergeable = False
			LOGGER.warning('PR is not mergeable. Not styling files.')
			# TODO: In this case, put a Pending status on the commit
		verified = verified and mergeable

		if not verified:
			LOGGER.warning('PR ' +
							str(self.payload['pull_request']["number"]) +
							' is not valid.')
		else:
			LOGGER.info('PR ' + str(self.payload['pull_request']["number"]) +
						' is valid.')
		return verified

	def git_process_pr(self):
		"""Process the git repo, merge the PR if mergeable.

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
				if (rem[1] == self.payload['pull_request']['base']['repo']['git_url']
				or rem[1] == self.payload['pull_request']['base']['repo']['ssh_url']):
					base_remote = rem[0]
					LOGGER.info('Base remote: ' + base_remote)
		if base_remote is None:
			raise PRHandlerException('Base remote does not exist yet, with URL ' +
							self.payload['pull_request']['base']['repo']['git_url'] +
							' Please create it first in the local git repo.')

		# update repo and check out base branch
		LOGGER.info('Getting the base branch')
		git_command('fetch ' + base_remote, self.repodir)
		base_branch_name = self.payload['pull_request']['base']['ref']
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
		pr_number = self.payload['pull_request']['number']
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

	def check_style(self, file_list):
		"""Check style of the given list of files"""
		LOGGER.info('Checking style of changed/added files')
		# Only check files touched by the PR which are also in the desired fileset:
		# `cpp` and `h` files in official `addons`, `examples`, `devApps`,
		# `libs/openFrameworks`)
		file_list = [filename for filename in file_list if
					filename.lower().endswith(('.cpp', '.h')) and
					filename.lower().startswith(('examples',
												'addons',
												'apps',
												'libs' + os.path.sep + 'openframeworks'))]
		LOGGER.info('Styling files')
		for tmp_file in file_list:
			style_file(os.path.abspath(os.path.join(self.repodir, tmp_file)),
						self.stylerdir)
		LOGGER.info('Finished styling. Checking if there were changes.')
		pr_number = self.payload['pull_request']['number']
		pr_url = self.payload['pull_request']['html_url']

		# check if styling changed any files
		if git_command('status --porcelain', self.repodir, True, False):
			patch_file_name = ('pr-' + str(pr_number) + '.patch')
			LOGGER.info('Changes detected. Creating patch file ' + patch_file_name)
			with open(os.path.join('patches', patch_file_name), 'w') as patchfile:
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

	def publish_results(self, result):
		"""Report back to PR, either via Github Status API or ofbot comments"""
		if my_config['feedback_method'] is "status":
			self.add_status(result)
		elif my_config['feedback_method'] is "comment":
			self.add_comment(result)
		else:
			raise PRHandlerException("Unknown feedback method: " +
							str(my_config['feedback_method']))

	def add_status(self, result):
		"""Add the relevant codestyle information via a PR Status"""
		LOGGER.info('Adding Status info to PR')
		repo = self.api_github.get_user('bilderbuchi').get_repo('openFrameworks')
		commit = repo.get_commit(self.payload['pull_request']['head']['sha'])
		# State: success, failure, error, or pending
		if result['patch_file_name']:
			# There's a patch file
			my_gist = self.create_gist(result)
			commit.create_status(state='failure',
								target_url=my_gist.html_url,
								description='PR does not conform to style. Click for details.')
		else:
			# no patch necessary-> green status
			commit.create_status(state='success',
								description='PR conforms to code style.')

	def add_comment(self, result):
		"""Add the relevant codestyle information via a comment on the thread"""
		# TODO: Implement this
		raise PRHandlerException('Feedback via comments not yet implemented.' +
									' Aborting.')

	def create_gist(self, result):
		"""Create gist with usage instructions and patch file.

		Return Gist object for further consumption"""
		LOGGER.info('Creating gist')
		with open('gist_description.md', 'r') as descfile:
			desc_string = descfile.read().format(result['pr_number'], result['pr_url'])
			with open(os.path.join('patches', result['patch_file_name']),
					'r') as patchfile:
				patch_file_name = 'pr-' + str(result['pr_number']) + '.patch'
				desc_file_name = ('OF_PR' + str(result['pr_number']) + '-' +
					self.payload['pull_request']['head']['sha'][1:7] +
					'.md')
				my_gist = self.api_github.get_user().create_gist(True,
					{desc_file_name: github.InputFileContent(desc_string),
					patch_file_name: github.InputFileContent(patchfile.read())},
					'OF Code style patch for PR ' + str(result['pr_number']))
		LOGGER.info('Created Gist ' + my_gist.html_url)
		return my_gist

	def clean_up(self):
		"""Clean up the repo"""
		LOGGER.info('Cleaning up.')
		git_command('checkout master', self.repodir)
		git_command('submodule update --init', self.repodir)


def git_command(arg_string, repo_dir, return_output=False, log_output=True):
	"""Execute git command in repo_dir and log output to LOGGER"""
	try:
		# the argument string has to be split if Shell==False in check_output
		output = subprocess.check_output(shlex.split('git ' + arg_string),
										stderr=subprocess.STDOUT, cwd=repo_dir)
		if output and log_output:
			LOGGER.debug(str(output).rstrip('\n'))
		if return_output:
			return str(output).rstrip('\n')
	except subprocess.CalledProcessError as exc:
		if log_output:
			LOGGER.error(str(exc.cmd) + ' failed with exit status ' +
						str(exc.returncode) + ':')
			LOGGER.error(exc.output)
		if return_output:
			return exc.output


def style_file(my_file, style_tool_dir):
	""" Call style tool on file and log output to LOGGER"""
	try:
		# the argument string has to be split if Shell==False in check_output
		output = subprocess.check_output(shlex.split('.' + os.path.sep +
													'ofStyler ' + my_file),
										stderr=subprocess.STDOUT, cwd=style_tool_dir)
		if output:
			LOGGER.debug(str(output).rstrip('\n'))
	except subprocess.CalledProcessError as exc:
		LOGGER.error(exc.cmd + ' failed with exit status ' +
					exc.returncode + ':')
		LOGGER.error(exc.output)


class PRHandlerException(Exception):
	pass


class My_endpoint:
	""" Endpoint for webserver"""

	def POST(self):
		""" React to a received POST request"""
		LOGGER.info("Received POST request.")
		if web.ctx.ip not in my_config['github_ips']:
			LOGGER.warning("Origin of request UNKNOWN: " + web.ctx.ip)
			return
		else:
			LOGGER.debug("Origin of request: " + web.ctx.ip)
		try:
			payload = json.loads(web.input()['payload'])
		except KeyError:
			# crutch: if an invalid request arrives locally, load a json file directly
			if web.ctx.ip == '127.0.0.1':
				with open('sample_payload.json') as sample:
					payload = json.load(sample)
			else:
				raise
#		LOGGER.debug('POST my_queue id: ' + str(id(my_queue)))
		sleep(0.5)  # to make sure the webserver has flushed  all log messages
		self.handle_payload(payload, my_queue)
		return

	@staticmethod
	def handle_payload(payload, queue):
		"""	Queue new PRs coming in during processing"""
		LOGGER.info('Received PR ' + str(payload['number']) + ': ' +
					payload['pull_request']['title'])
		with open('last_payload.json', 'w') as outfile:
			json.dump(payload, outfile, indent=2)
		LOGGER.debug("handing payload off to queue")
		queue.put(payload)


def main():
	"""Main function"""
	if len(sys.argv) == 1:
		sys.argv.append(str(my_config['local_port']))
	threaded_pr_worker = PrHandler(my_queue)
	threaded_pr_worker.daemon = True
	threaded_pr_worker.start()
#	LOGGER.debug('outer my_queue id: ' + str(id(my_queue)))
	app = web.application(URLS, globals())
	app.run()
	my_queue.join()

if __name__ == "__main__":
	main()
