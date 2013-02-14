#!/usr/bin/python
"""Make sure openFrameworks Pull Requests conform to the code style"""
import styleguard
import logging
import json
import os
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
			location = os.getenv('OPENSHIFT_REPO_DIR', '')
			with open(os.path.join(location, 'sample_payload.json'), 'r') as sample:
				payload = json.load(sample)
		else:
			raise
	styleguard.handle_payload(payload)
	return 'OK'


def main():
	"""Main function"""
	# Instantiate a PrHandler, which start waiting on styleguard.MY_QUEUE
	LOGGER.debug('In ofCodeStyleGuard main function')
	_threaded_pr_worker = styleguard.PrHandler()
	APP.run(host='0.0.0.0', port=styleguard.cfg['local_port'])
	styleguard.MY_QUEUE.join()

if __name__ == "__main__":
	main()
