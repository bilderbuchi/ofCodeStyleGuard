import logging

# dictionary with configuration parameters
cfg = dict(
	repo_git_url="git://github.com/bilderbuchi/openFrameworks.git",
	storage_dir='data/',
	repo_local_path="openFrameworks_files/",
	style_tool_path='openFrameworks_files/scripts/dev/style',
	fetch_method='file',  # 'git' or 'file'
	# git will maintain a local repo, file will fetch fresh PR files on every run
	feedback_method="status",
	# 'status' for using the GH Status API, 'comment' for using normal comments
	github_ips={'207.97.227.253', '50.57.128.197', '108.171.174.178',
				'50.57.231.61', '127.0.0.1'},
	logging_level=logging.DEBUG,  # DEBUG/INFO/WARNING/ERROR/CRITICAL,
#	logfile = "logfile.log",
	local_port=4896,
	authfile='auths.json'
)
