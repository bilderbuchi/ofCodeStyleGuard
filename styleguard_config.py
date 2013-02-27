""" Configuration for styleguard"""
import logging
import os

# dictionary with configuration parameters
cfg = dict(  # pylint: disable=C0103
	repo_git_url="git://github.com/bilderbuchi/openFrameworks.git",
	storage_dir=os.getenv('OPENSHIFT_DATA_DIR', 'data/'),
	repo_local_path="openFrameworks_files/",
	styler_local_path='styler_files/',
	fetch_method='file',  # 'git' or 'file'
	# git will maintain a local repo, file will fetch fresh PR files on every run
	feedback_method="status",
	# 'status' for using the GH Status API, 'comment' for using normal comments
	suppress_feedback=False,  # only create gists, don't affect the checked PR
	logging_level=logging.DEBUG,  # DEBUG/INFO/WARNING/ERROR/CRITICAL,
	logfile='ofCodeStyleGuard.log',
	authfile='auths.json',
#
#	Web server configuration:
	local_port=os.getenv('OPENSHIFT_INTERNAL_PORT', 4896),
	github_ips=['207.97.227.253', '50.57.128.197', '108.171.174.178',
				'50.57.231.61', '54.235.183.49', '54.235.183.23',
				'54.235.118.251', '54.235.120.57', '54.235.120.61',
				'54.235.120.62', '127.0.0.1']
)
