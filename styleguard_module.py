import logging
import Queue

my_config = dict(
	repo_git_url="git://github.com/bilderbuchi/openFrameworks.git",
	repo_local_path="openFrameworks/",
	style_tool_path='openFrameworks/scripts/dev/style',
	feedback_method="status",
	# 'status' for using the GH Status API, 'comment' for using normal comments
	github_ips={'207.97.227.253', '50.57.128.197', '108.171.174.178',
				'50.57.231.61', '127.0.0.1'},
	logging_level=logging.DEBUG,  # DEBUG/INFO/WARNING/ERROR/CRITICAL,
#	logfile = "logfile.log",
	local_port=4896,
	authfile='auths.json'
)

# ugly hack/workaround for web.py globals issue
my_queue = Queue.Queue()
