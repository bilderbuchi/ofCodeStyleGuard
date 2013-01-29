#!/usr/bin/python
import web
import logging
import json
import threading
import sys
import os
from github import Github
from time import sleep
from styleguard_module import my_config, my_queue

logger = logging.getLogger(' ')
logging.basicConfig(level=my_config['logging_level'])
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
		self.API_Github = self.init_authentication()
		if self.API_Github == 1:
			logger.critical('Initialization failed. Aborting.')
			sys.exit()
		
	def run(self):
		while True:
			logger.info("Waiting in worker run()")
#			logging.debug('run self.queue id: ' + str(id(self.queue)))
			payload = self.queue.get()
			logger.info("Aquired new payload: PR " + str(payload["number"]))
			if self.validate_PR(payload):
				self.git_process_PR()
				self.check_style()
				self.publish_results()
				self.teardown()
			sleep(5)
			self.queue.task_done()
			logger.info("Finished processing payload PR " + str(payload["number"]))
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
			
	def validate_PR(self, payload):
		logger.info('Verifying information from payload')
		if payload['repository']['git_url'] == my_config['repo_git_url']:
			verified = True
		else:
			verified = False
			logger.warning('PR git_url ' + payload['repository']['git_url'] + ' does not match config: ' + my_config['repo_git_url'])
		if payload['action'] != 'closed':
			verified = verified and True
		else:
			verified = False
			logger.warning('PR is closed!')
		if payload['pull_request']['merged'] == False:
			verified = verified and True
		else:
			verified = False
			logger.warning('PR is merged!')
		if payload['pull_request']['mergeable'] == True:
			mergeable = True	
		else:
			# It's possible that mergeable is incorrectly false.
			# Maybe due to being set by a check procedure after firing off the POST request?
			# check again online, if not, then mergeable = False
			logger.info("Re-checking if PR is mergeable")
			sleep(5)
			if self.API_Github.get_repo(payload['repository']['full_name']).get_pull(payload['pull_request']['number']).mergeable == True:
				mergeable = True
			else:
				mergeable = False
				logger.warning('PR is not mergeable')
		verified = verified and mergeable
		
		if not verified:
			logger.warning('PR ' + str(payload["number"]) + ' is not valid.')
		else:
			logger.info('PR ' + str(payload["number"]) + ' is valid.')
		return verified
	
	def git_process_PR(self):
		#* if PR in question is mergeable, pull down and merge.
		logger.info('Starting git processing of PR')
		os.chdir(self.basedir)
		os.chdir(my_config['repo_local_path'])
		logging.debug('In directory ' + str(os.getcwd()))
		
#		payload['pull_request']['head']['repo']['ssh_url']
#		payload['pull_request']['head']['ref'] -> testbranch
#		
#		payload['pull_request']['base']['repo']['ssh_url']
#		payload['pull_request']['base']['ref'] -> master
#		
#		payload['pull_request']['head']['sha'] -> last commit
		
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
		os.chdir(self.basedir)

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

