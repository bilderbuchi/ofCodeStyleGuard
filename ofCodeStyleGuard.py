#!/usr/bin/python
import web
import logging
import json
import threading
import sys
import os
import git
import subprocess
import shlex
import github
from time import sleep
from styleguard_module import my_config, my_queue

# TODO: make git_command class method
# TODO: refactor out git-python
# TODO: Implement manual triggering of PR checks
# TODO: Implement proper abort/bail system
# TODO: web.py -> flask/Werkzeug

logger = logging.getLogger(' ')
logging.basicConfig(level=my_config['logging_level'])
if my_config['logging_level'] == logging.DEBUG:
	os.environ['GIT_PYTHON_TRACE'] = "full"  # '1' or 'full' for including output
else:
	os.environ['GIT_PYTHON_TRACE'] = "0"
URLS = (
	'/', 'My_endpoint'
)


class PrHandler(threading.Thread):
	"""Threaded PR Worker"""

	def __init__(self, queue):
		logger.debug("Starting PR worker thread")
		threading.Thread.__init__(self)
		self.queue = queue
		self.payload = None
		self.basedir = os.getcwd()  # base directory
		self.repodir = os.path.abspath(os.path.join(self.basedir,
													my_config['repo_local_path']))
		self.stylerdir = os.path.abspath(os.path.join(self.basedir,
													my_config['style_tool_path']))
		# TODO: Verify uncrustify minimum version
		self.api_github = self.init_authentication()
		if self.api_github == 1:
			logger.critical('Initialization failed. Aborting.')
			sys.exit()
		self.repo = git.Repo(self.repodir)
		logger.info('Local git repo at ' + str(self.repo.working_dir))
		logger.info('Checking repo')
		if self.repo.is_dirty():
			logger.critical('Local git repo is dirty! Correct this first!')
			sys.exit()

	def run(self):
		while True:
			logger.info("Waiting in worker run()")
#			logger.debug('run self.queue id: ' + str(id(self.queue)))
			self.payload = self.queue.get()
			logger.info("Aquired new payload: PR " + str(self.payload["number"]))
			# TODO: guaranteeing clean_up could be done with exceptions!
			if self.validate_pr():
				changed_files = self.git_process_pr()
				result = self.check_style(changed_files)
				self.publish_results(result)
				self.clean_up()
			else:
				logger.info('Skipping PR ' + str(self.payload["number"]))
			self.queue.task_done()
			logger.info("Finished processing payload PR " + str(self.payload["number"]))
			logger.debug("self.queue size: " + str(self.queue.qsize()))

	def init_authentication(self):
		"""Create appropriate Github API user"""
		logger.info('Creating API-authenticated user')
		with open(my_config['authfile'], 'r') as authfile:
			auths_temp = json.load(authfile)
		if my_config['feedback_method'] == "status":
			if all(scope in auths_temp['ofbot_codestyle_status']['scopes']
					for scope in ['repo:status', 'gist']):
				# Return authorized PyGithub Github API instance
				g = github.Github(auths_temp['ofbot_codestyle_status']['token'])
				# Verification of authentication
				try:
					unused_var = g.get_user().name
				except github.GithubException as exception:
					# will throw 401 {u'message': u'Bad credentials'}
					logger.critical('Authentication invalid: ' +
								str(exception.status) + " " + str(exception.data))
					return 1
				return g
			else:
				logger.error('Could not authenticate for Status API' +
							' with auth ofbot_codestyle_status')
				return 1
		elif my_config['feedback_method'] == "comment":
			logger.critical('Comment authorization not yet implemented!')
#			TODO: implement comment-style PR feedback
			return 1
		else:
			logger.error("Unknown feedback method: " +
							str(my_config['feedback_method']))
			return 1

	def validate_pr(self):
		"""Determine if the current PR is valid for processing"""
		logger.info('Verifying information from payload')
		if self.payload['repository']['git_url'] == my_config['repo_git_url']:
		# TODO: be more robust about git/ssh URLs
			verified = True
		else:
			verified = False
			logger.warning('PR git_url ' +
							self.payload['repository']['git_url'] +
							' does not match config: ' + my_config['repo_git_url'])
		if self.payload['action'] != 'closed':
			verified = verified and True
		else:
			verified = False
			logger.warning('PR is closed!')
		if self.payload['pull_request']['merged'] == False:
			verified = verified and True
		else:
			verified = False
			logger.warning('PR is merged!')

		# Mergeability checking is asynchronous on Github, so this has to be
		# confirmed after receipt of the PR
		logger.info("Checking if PR is mergeable")
		sleep(5)
		# TODO: this prints an unwanted message to console
		if (self.api_github.get_repo(self.payload['repository']['full_name'])
							.get_pull(self.payload['pull_request']['number'])
							.mergeable == True):
			mergeable = True
		else:
			mergeable = False
			logger.warning('PR is not mergeable')
			# TODO: In this case, put an Error status on the commit
		verified = verified and mergeable

		if not verified:
			logger.warning('PR ' +
							str(self.payload['pull_request']["number"]) +
							' is not valid.')
		else:
			logger.info('PR ' + str(self.payload['pull_request']["number"]) +
						' is valid.')
		return verified

	def git_process_pr(self):
		"""Process the git repo, merge the PR if mergeable.

		Return the list of files added or modified in the PR"""
		logger.info('Starting git processing of PR')
		logger.debug(self.repo.git.checkout('master'))
		git_command('submodule update --init', self.repodir)

		# Identify remotes, and create their objects
		base_remote = None
		head_remote = None
		# TODO: nicer handling of remotes, both git and ssh urls
		for rem in self.repo.remotes:
			logger.debug('Found remote ' + rem.name + ': ' + rem.url)
			if rem.url == self.payload['pull_request']['base']['repo']['git_url']:
				base_remote = rem
				logger.info('Base remote: ' + base_remote.name)
			if rem.url == self.payload['pull_request']['head']['repo']['git_url']:
				head_remote = rem
				logger.info('Head remote: ' + head_remote.name)
		if base_remote is None:
			logger.critical('Base remote does not exist yet, with URL ' +
							self.payload['pull_request']['base']['repo']['git_url'])
			logger.critical('Please create it first in the local git repo.')
			sys.exit()

		# update repo and check out base branch
		base_remote.fetch()
		base_branch_name = self.payload['pull_request']['base']['ref']
		logger.debug('Base branch name: ' + base_branch_name)

		if not git_command('show-ref --verify --heads --quiet -- refs/heads/' +
				base_branch_name, self.repodir):
			# branch already exists, check it out
			git_command('checkout ' + base_branch_name, self.repodir)
			git_command('merge ' + base_remote.name + '/' +
					base_branch_name, self.repodir)
		else:
			# branch does not exist locally yet, get it
			git_command('checkout -b ' + base_branch_name + ' ' +
					base_remote.name + '/' + base_branch_name, self.repodir)
		logger.debug(self.repo.git.submodule('update', '--init'))

		logger.info('Getting the PR branch')
		# TODO: log current head commit here
		pr_number = self.payload['pull_request']['number']
		pr_branch_name = 'pr-' + str(pr_number)
		self.repo.git.fetch(self.payload['pull_request']['head']['repo']['git_url'],
							'pull/' + str(pr_number) + '/head:' + pr_branch_name)
		self.repo.git.checkout(pr_branch_name)
		logger.debug(self.repo.git.submodule('update', '--init'))

		logger.info('Determine files added/modified in the PR')
		changed_files = git_command('diff --name-only --diff-filter=AM ' +
									base_branch_name + '...' +
									pr_branch_name, self.repodir, True, False)

		logger.debug(self.repo.git.checkout(pr_branch_name))
		logger.debug(self.repo.git.submodule('update', '--init'))
		# after this, we have a clean git repo with the PR branch checked out
		return changed_files.split()

	def check_style(self, file_list):
		"""Check style of the given list of files"""
		logger.info('Checking style of changed/added files')
		# Only check files touched by the PR which are also in the desired fileset:
		# `cpp` and `h` files in official `addons`, `examples`, `devApps`,
		# `libs/openFrameworks`)
		file_list = [filename for filename in file_list if
					filename.lower().endswith(('.cpp', '.h')) and
					filename.lower().startswith(('examples',
												'addons',
												'apps',
												'libs' + os.path.sep + 'openframeworks'))]
#		logger.debug('Filtered list of files to be style-checked:')
#		for tmp_f in file_list:
#			logger.debug(tmp_f)
		logger.info('Styling files')
		for tmp_file in file_list:
			style_file(os.path.abspath(os.path.join(self.repodir, tmp_file)),
						self.stylerdir)
		logger.info('Finished styling. Checking if there were changes.')
		pr_number = self.payload['pull_request']['number']
		pr_url = self.payload['pull_request']['html_url']
		# check if styling changed any files
		if git_command('status --porcelain', self.repodir, True, False):
			patch_file_name = ('pr-' + str(pr_number) + '.patch')
			logger.info('Creating patch file ' + patch_file_name)
			with open(os.path.join('patches', patch_file_name), 'w') as patchfile:
				# OK to use git diff since only text files will be modified
				patchfile.write(git_command('diff HEAD', self.repodir, True, False))
			# test if patch applies cleanly
			git_command('reset --hard HEAD', self.repodir)
			if git_command('apply --index --check ' +
						os.path.join(self.basedir, 'patches', patch_file_name),
						self.repodir, True):
				logger.critical('Patch' + patch_file_name +
								' does not apply cleanly, aborting!')
				sys.exit()
			else:
				logger.info('Patch ' + patch_file_name + ' applies cleanly')
		else:
			patch_file_name = ''
			logger.info("PR already conforms to style")

		# now, reset HEAD so that the repo is clean again
		logging.debug('Resetting HEAD to get a clean repo')
		git_command('reset --hard HEAD', self.repodir)
		logger.debug(self.repo.git.submodule('update', '--init'))
		# TODO: check if the merge itself still works:
		# git format-patch master --stdout | git-apply --check

		# *optional*: perform code style of OF itself to determine initial state
		# This could be stored in gists.
		# Clean up list of changed files
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
			logger.error("Unknown feedback method: " +
							str(my_config['feedback_method']))

	def add_status(self, result):
		"""Add the relevant codestyle information via a PR Status"""
		logger.info('Adding Status info to PR')
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
		# self.payload['pull_request']['head']['sha'] -> last commit
		# commits=user.get_repo('openFrameworks').get_pull(1).get_commits()
		# c.create_status(state='success',description='somestatus',
		# target_url='http://sdfsdite.com')

	def add_comment(self, result):
		"""Add the relevant codestyle information via a comment on the thread"""
		logger.critical('Feedback via comments not yet implemented. Aborting.')
		sys.exit()

	def create_gist(self, result):
		"""Create gist with usage instructions and patch file.

		Return Gist object for further consumption"""
		logger.info('Creating gist')
		with open('gist_description.md', 'r') as descfile:
			desc_string = descfile.read().format(result['pr_number'], result['pr_url'])
			with open(os.path.join('patches', result['patch_file_name']), 'r') as patchfile:
				patch_file_name = 'pr-' + str(result['pr_number']) + '.patch'
				desc_file_name = ('OF_PR' + str(result['pr_number']) + '-' +
					self.payload['pull_request']['head']['sha'][1:7] +
					'.md')
				my_gist = self.api_github.get_user().create_gist(True,
					{desc_file_name: github.InputFileContent(desc_string),
					patch_file_name: github.InputFileContent(patchfile.read())},
					'OF Code style patch for PR ' + str(result['pr_number']))
		logger.info('Created Gist ' + my_gist.html_url)
		return my_gist

	def clean_up(self):
		"""Clean up the repo and reset cwd"""
		# TODO: make sure this executes in any case!
		# os.chdir(self.basedir)
		logger.debug(self.repo.git.checkout('master'))
		git_command('submodule update --init', self.repodir)


def git_command(arg_string, repo_dir, return_output=False, log_output=True):
	"""Execute git command in repo_dir and log output to logger"""
	try:
		# the argument string has to be split if Shell==False in check_output
		output = subprocess.check_output(shlex.split('git ' + arg_string),
										stderr=subprocess.STDOUT, cwd=repo_dir)
		if output and log_output:
			logger.debug(output)
		if return_output:
			return output
	except subprocess.CalledProcessError as exc:
		if log_output:
			logger.error(str(exc.cmd) + ' failed with exit status ' +
						str(exc.returncode) + ':')
			logger.error(exc.output)
		if return_output:
			return exc.output


def style_file(my_file, style_tool_dir):
	""" Call style tool on file and log output to logger"""
	try:
		# the argument string has to be split if Shell==False in check_output
		output = subprocess.check_output(shlex.split('.' + os.path.sep +
													'ofStyler ' + my_file),
										stderr=subprocess.STDOUT, cwd=style_tool_dir)
		if output:
			logger.debug(output.rstrip('\n'))
	except subprocess.CalledProcessError as exc:
		logger.error(exc.cmd + ' failed with exit status ' +
					exc.returncode + ':')
		logger.error(exc.output)


class My_endpoint:

	def POST(self):
		logger.info("Received POST request.")
		if web.ctx.ip not in my_config['github_ips']:
			logger.warning("Origin of request UNKNOWN: " + web.ctx.ip)
			return
		else:
			logger.debug("Origin of request: " + web.ctx.ip)
		try:
			payload = json.loads(web.input()['payload'])
		except KeyError:
			# crutch: if an invalid request arrives locally, load a json file directly
			if web.ctx.ip == '127.0.0.1':
				with open('sample_payload.json') as sample:
					payload = json.load(sample)
			else:
				raise
#		logger.debug('POST my_queue id: ' + str(id(my_queue)))
		sleep(0.5)  # to make sure the webserver has flushed  all log messages
		self.handle_payload(payload, my_queue)
		return

	def handle_payload(self, payload, queue):
		"""	Queue new PRs coming in during processing"""
		logger.info('Received PR ' + str(payload['number']) + ': ' +
					payload['pull_request']['title'])
		with open('last_payload.json', 'w') as outfile:
			json.dump(payload, outfile, indent=2)
		logger.debug("handing payload off to queue")
		queue.put(payload)


def main():
	if len(sys.argv) == 1:
		sys.argv.append(str(my_config['local_port']))
	threaded_pr_worker = PrHandler(my_queue)
	threaded_pr_worker.daemon = True
	threaded_pr_worker.start()
#	logger.debug('outer my_queue id: ' + str(id(my_queue)))
	app = web.application(URLS, globals())
	app.run()
	my_queue.join()

if __name__ == "__main__":
	main()
