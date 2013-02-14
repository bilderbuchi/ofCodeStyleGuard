""" Configuration for styleguard"""
import logging
import os

# dictionary with configuration parameters
cfg = dict(
	repo_git_url="git://github.com/bilderbuchi/openFrameworks.git",
	storage_dir=os.getenv('OPENSHIFT_DATA_DIR', 'data/'),
	repo_local_path="openFrameworks_files/",
	style_tool_path='openFrameworks_files/scripts/dev/style',
	fetch_method='file',  # 'git' or 'file'
	# git will maintain a local repo, file will fetch fresh PR files on every run
	feedback_method="status",
	# 'status' for using the GH Status API, 'comment' for using normal comments
	logging_level=logging.DEBUG,  # DEBUG/INFO/WARNING/ERROR/CRITICAL,
#	logfile = "logfile.log",
	authfile='auths.json',
#
#	Web server configuration:
	local_port=os.getenv('OPENSHIFT_INTERNAL_PORT', 4896),
	github_ips=['207.97.227.253', '50.57.128.197', '108.171.174.178',
				'50.57.231.61', '127.0.0.1']
)
