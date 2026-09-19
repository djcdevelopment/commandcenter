"""Mount only the bounded controller tools, reusing existing HEARTH providers."""
from .fs import read_file, list_dir, glob_files
from .git import git_status, git_log
from .knowledge import query_knowledge, query_beliefs_summary
from .rungstate import query_rung_state
from .rotation import rotation_status
from .scheduler import capture_resource_snapshot, propose_schedule
from .task_lane import submit_task, task_status
from .build_requests import (create_build_request, get_build_request, list_build_requests,
                             execute_build_request, update_build_request, close_build_request)


def get_tools():
    return [read_file, list_dir, glob_files, git_status, git_log, query_knowledge,
            query_beliefs_summary, query_rung_state, rotation_status,
            capture_resource_snapshot, propose_schedule, submit_task, task_status,
            create_build_request, get_build_request, list_build_requests,
            execute_build_request, update_build_request, close_build_request]
