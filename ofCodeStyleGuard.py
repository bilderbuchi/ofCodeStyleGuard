#!/usr/bin/python
"""Make sure openFrameworks Pull Requests conform to the code style"""
import styleguard
import logging
import json
from flask import Flask, request

LOGGER = logging.getLogger('webserver')
logging.basicConfig(level=styleguard.cfg['logging_level'])
APP = Flask(__name__)
APP.logger.setLevel(styleguard.cfg['logging_level'])
logging.getLogger('urllib3').setLevel(logging.WARNING)


@APP.route('/', methods=['POST'])
def api_pr():
	""" React to a received POST request"""
	LOGGER.info("Received POST request.")

	if request.remote_addr not in styleguard.cfg['github_ips']:
		LOGGER.warning("Origin of request UNKNOWN: " + request.remote_addr)
		return
	else:
		LOGGER.debug("Origin of request: " + request.remote_addr)

	try:
		payload = json.loads(request.form['payload'])
	except KeyError:
		# crutch: if an invalid request arrives locally, load a json file directly
		if request.remote_addr == '127.0.0.1':
			with open('sample_payload.json') as sample:
				payload = json.load(sample)
		else:
			raise
	handle_payload(payload, styleguard.MY_QUEUE)
	return 'OK'


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
	# Instantiate a PrHandler, which start waiting on styleguard.MY_QUEUE
	_threaded_pr_worker = styleguard.PrHandler()
	APP.run(host='0.0.0.0', port=styleguard.cfg['local_port'])
	styleguard.MY_QUEUE.join()

if __name__ == "__main__":
	main()
