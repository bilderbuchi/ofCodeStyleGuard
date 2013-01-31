#!/usr/bin/python
import web
import logging
import json
import threading
import sys
import os
import git
import subprocess, shlex
from github import Github
from time import sleep
from styleguard_module import my_config, my_queue

logger = logging.getLogger(' ')
logging.basicConfig(level=my_config['logging_level'])
if my_config['logging_level'] == logging.DEBUG:
	os.environ['GIT_PYTHON_TRACE'] = "full" # '1' or 'full' for including output
else:
	os.environ['GIT_PYTHON_TRACE'] = "0"
urls = (
	'/', 'my_endpoint'
)

class PR_handler(threading.Thread):
	"""Threaded PR Worker"""
	
	def __init__(self, queue):
		logger.debug("Starting PR worker thread")
		threading.Thread.__init__(self)
		self.queue = queue
		self.basedir = os.getcwd() #base directory
		self.repodir = os.path.abspath(os.path.join(self.basedir,my_config['repo_local_path']))
		self.API_Github = self.init_authentication()
		if self.API_Github == 1:
			logger.critical('Initialization failed. Aborting.')
			sys.exit()
		self.repo = git.Repo(self.repodir)
		logger.info('Local git repo at ' + str(self.repo.working_dir))
		logger.info('Checking repo')
		if self.repo.is_dirty():
			logger.critical('Local git repo is dirty! Correct this first!')
			sys.exit()
		self.integration_branch_name='integration-branch'
		
	def run(self):
		while True:
			logger.info("Waiting in worker run()")
#			logging.debug('run self.queue id: ' + str(id(self.queue)))
			self.payload = self.queue.get()
			logger.info("Aquired new payload: PR " + str(self.payload["number"]))
			if self.validate_PR():
				self.git_process_PR()
				self.check_style()
				self.publish_results()
				sleep(5)
				self.teardown()
			else:
				logger.info('Skipping PR ' + str(self.payload["number"]))
			self.queue.task_done()
			logger.info("Finished processing payload PR " + str(self.payload["number"]))
			logger.debug("self.queue size: " + str(self.queue.qsize()))
			
	def init_authentication(self):
		logger.info('Creating API-authenticated user')
		with open(my_config['authfile'],'r') as authfile:
			auths_temp = json.load(authfile)
		if my_config['feedback_method'] == "status":
			if all(scope in auths_temp['Codestyle_status_access']['scopes'] for scope in ['repo:status','gist']):
				# Create authorized PyGithub Github API instance
				API_Github = Github(auths_temp['Codestyle_status_access']['token'])
#					TODO: Verification of authentication
#					possibly by catching an exception when checking out the base repo
				return API_Github
			else:
				logging.error('Could not authenticate for Status API with auth Codestyle_status_access')
				return 1
		elif my_config['feedback_method'] == "comment":
			logging.critical('Comment authorization not yet implemented!')
#			TODO: implement this
			return 1
		else:
			logging.error("Unknown feedback method: " + str(my_config['feedback_method']))
			return 1
			
	def validate_PR(self):
		logger.info('Verifying information from payload')
		if self.payload['repository']['git_url'] == my_config['repo_git_url']:
			verified = True
		else:
			verified = False
			logger.warning('PR git_url ' + self.payload['repository']['git_url'] + ' does not match config: ' + my_config['repo_git_url'])
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
		if self.payload['pull_request']['mergeable'] == True:
			mergeable = True	
		else:
			# It's possible that mergeable is incorrectly false.
			# Maybe due to being set by a check procedure after firing off the POST request?
			# check again online, if not, then mergeable = False
			logger.info("Re-checking if PR is mergeable")
			sleep(5)
			if self.API_Github.get_repo(self.payload['repository']['full_name']).get_pull(self.payload['pull_request']['number']).mergeable == True:
				mergeable = True
			else:
				mergeable = False
				logger.warning('PR is not mergeable')
		verified = verified and mergeable
		
		if not verified:
			logger.warning('PR ' + str(self.payload['pull_request']["number"]) + ' is not valid.')
		else:
			logger.info('PR ' + str(self.payload['pull_request']["number"]) + ' is valid.')
		return verified
	
	def git_process_PR(self):
		#* if PR in question is mergeable, pull down and merge.
		logger.info('Starting git processing of PR')
		# Identify remotes, and create their objects
		base_remote = None
		head_remote = None
		for rem in self.repo.remotes:
			logger.debug('Found remote ' + rem.name + ': ' + rem.url)
			if rem.url == self.payload['pull_request']['base']['repo']['git_url']:
				base_remote = rem
				logger.info('Base remote: ' + base_remote.name)
			if rem.url == self.payload['pull_request']['head']['repo']['git_url']:
				head_remote = rem
				logger.info('Head remote: ' + head_remote.name)
		if base_remote is None:
			logger.critical('Base remote does not exist yet, with URL ' + self.payload['pull_request']['base']['repo']['git_url'])
			logger.critical('Please create it first.')
			sys.exit()
			
		# update repo and check out base branch
		# TODO: this is not yet clean and waterproof!!
		base_remote.fetch()
		base_branch_name = self.payload['pull_request']['base']['ref']
		logger.debug('Base branch name: ' + base_branch_name)
		logger.debug(self.repo.git.checkout(B=base_branch_name))
		base_remote.pull()
		logger.debug(self.repo.git.submodule('update', '--init'))

		# create out temporary branch
		logger.debug(self.repo.git.branch(self.integration_branch_name))

		# pull down the PR branch
		pr_number = self.payload['pull_request']['number']
		pr_branch_name = 'pr-' + str(pr_number)
		self.repo.git.fetch(self.payload['pull_request']['head']['repo']['git_url'], 'pull/' + str(pr_number) + '/head:' + pr_branch_name)
		self.repo.git.checkout(pr_branch_name)
		logger.debug(self.repo.git.submodule('update', '--init'))
			# from gist:  git fetch <remote> pull/7324/head:pr-7324
			# maybe:
			# git checkout -B pr-7324
			# git pull origin pull/7324/head
		logger.debug(self.repo.git.checkout(self.integration_branch_name))
		logger.debug(self.repo.git.merge(pr_branch_name))
		logger.debug(self.repo.git.submodule('update', '--init'))
#		self.payload['pull_request']['head']['sha'] -> last commit
		
	def check_style(self):
		#* *optional*: perform code style of OF itself to determine initial state
		#* perform code style analysis on PR. 
		# Only check files touched by the PR which are also in the desired fileset:
		# (`cpp` and `h` files in official `addons`, `examples`, `devApps`, `libs/openFrameworks`)
		#* determine if changes were necessary. If yes, formulate `diff` or `patch` with the needed corrections. 
		# This could be stored in gists.
		pass
	
	def publish_results(self):
		#* Report back to PR thread, either via Github Status API or ofbot comments
		if my_config['feedback_method'] is "status":
			self.add_status()
		elif my_config['feedback_method'] is "comment":
			self.add_comment()
		else:
			logging.error("Unknown feedback method: " + str(my_config['feedback_method']))
	
	def add_status(self):
#		commits=user.get_repo('openFrameworks').get_pull(1).get_commits()
	#c.create_status(state='success',description='somestatus',target_url='http://sdfsdite.com')
		pass
	
	def add_comment(self):
		logging.critical('Feedback via comments not yet implemented. Aborting.')
		sys.exit()
		
	def teardown(self):
		# TODO: make sure this executes in any case!
		os.chdir(self.basedir)
		logger.debug(self.repo.git.checkout('master'))
#		logger.debug(self.repo.delete_head(D=self.integration_branch_name))
		git_command('branch -D ' + self.integration_branch_name, self.repodir)

def git_command(argument_string, repo_dir):
	"""Execute git command in repo_dir and log output to logger"""
	try:
		# the argument string has to be split if Shell==False in check_output
		output = subprocess.check_output(shlex.split('git ' + argument_string), stderr=subprocess.STDOUT, cwd=repo_dir)
		logger.debug(output)
	except subprocess.CalledProcessError as CPE:
		logger.error(CPE.cmd + ' failed with exit status ' + CPE.returncode + ':')
		logger.error(CPE.output)

class my_endpoint:

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
#		logging.debug('POST my_queue id: ' + str(id(my_queue)))
		self.handle_payload(payload, my_queue)
		return
	
	def handle_payload(self,payload,queue):
		"""	Queue new PRs coming in during processing"""
		logger.debug('PR nr ' + str(payload['number']) + ': ' + payload['pull_request']['title'])
		with open('last_payload.json', 'w') as outfile:
	  		json.dump(payload, outfile, indent=2)
		logger.debug("handing payload off to queue")
		queue.put(payload)
		logger.debug("queue size: " + str(queue.qsize()))
	
def main():
	if len(sys.argv) == 1:
		sys.argv.append(str(my_config['local_port']))
	threaded_pr_worker = PR_handler(my_queue)
	threaded_pr_worker.daemon = True
	threaded_pr_worker.start()
#	logging.debug('outer my_queue id: ' + str(id(my_queue)))
	app = web.application(urls, globals())
	app.run()
	my_queue.join()
	
if __name__ == "__main__":
	main()

