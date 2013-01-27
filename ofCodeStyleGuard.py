#!/usr/bin/python
import web
import logging
import json
import Queue
import threading
import sys
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
		logger.debug("self.queue size: " + str(self.queue.qsize()))
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
			sleep(5)
			self.queue.task_done()
			logger.info("Finished processing payload PR " + str(payload["number"]))
			logger.debug("self.queue size: " + str(self.queue.qsize()))
			
	def validate_PR(self,payload):
		logger.debug('Verifying information from payload')
		verified = (payload['repository']['git_url'] == my_config['repo_git_url'])
		verified = verified and (payload['action'] != 'closed')
		verified = verified and (payload['pull_request']['merged'] == False)
		if payload['pull_request']['mergeable'] != True:
			# It's possible that mergeable is incorrectly false. Maybe due to being set after firing off the POST request
			# TODO: check again online, if not, then mergeable = False
			mergeable = True # this is wrong for now
			pass
		else:
			mergeable = True
		verified = verified and mergeable
		
		if not verified:
			logger.warning('PR ' + str(payload["number"]) + ' is not valid.')
		else:
			logger.info('PR ' + str(payload["number"]) + ' is valid.')
		return verified
	
	def git_process_PR(self):
		#* if PR in question is mergeable, pull down and merge. Should be possible without `PyGithub`.
		pass
	
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
			logging.error("Unknown feedback method: " + str(myconfig['feedback_method']))
		pass
	
	def add_status(self):
		pass
	
	def add_comment(self):
		pass

class my_endpoint:
#	def GET(self):
#		pass
#		#return "Hello, world!"

	def POST(self):
		logger.info("Received POST request.")
		if web.ctx.ip not in my_config['github_ips']:
			logger.warning("Origin of request UNKNOWN: " + web.ctx.ip)
			return
		else:
			logger.debug("Origin of request: " + web.ctx.ip)

		payload=json.loads(web.input()['payload'])
#		logging.debug('POST my_queue id: ' + str(id(my_queue)))
		self.handle_payload(payload,my_queue)
		return
	
	def handle_payload(self,payload,queue):
		"""	Queue new PRs coming in during processing"""
		logger.debug('PR nr ' + str(payload['number']) + ': ' + payload['pull_request']['title'])
		with open('last_payload.json', 'w') as outfile:
	  		json.dump(payload, outfile, indent=1)
		logger.debug("handling payload")
		queue.put(payload)
		logger.debug("queue size: " + str(queue.qsize()))
	
def main():
	if len(sys.argv) == 1:
		sys.argv.append(str(my_config['local_port']))
	threaded_pr_worker = PR_handler(my_queue)
	threaded_pr_worker.daemon=True
	threaded_pr_worker.start()
#	logging.debug('outer my_queue id: ' + str(id(my_queue)))
	app = web.application(urls, globals())
	app.run()
	my_queue.join()
	
if __name__ == "__main__":
	main()	