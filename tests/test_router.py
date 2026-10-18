import pytest

from zane.core.intent_router import IntentRouter

R = IntentRouter()


@pytest.mark.parametrize("text,tool,args", [
    ("Open VS Code.", "app_open", {"name": "VS Code"}),
    ("ZANE, launch Terminal", "app_open", {"name": "Terminal"}),
    ("Could you please open Safari?", "app_open", {"name": "Safari"}),
    ("Launch Activity Monitor", "app_open", {"name": "Activity Monitor"}),
    ("Open my Downloads folder", "folder_open", {"location": "downloads"}),
    ("quit Safari", "app_quit", {"name": "Safari"}),
    ("How much storage is available?", "sys_disk", {}),
    ("Check my battery status.", "sys_battery", {}),
    ("Show memory pressure.", "sys_memory", {}),
    ("Which process is consuming the most resources?", "sys_processes", {}),
    ("Check whether Python is installed.", "sys_dev_env", {}),
    ("Is my development environment configured correctly?", "sys_dev_env", {}),
    ("Give me my daily briefing", "task_briefing", {}),
    ("Show me all overdue tasks.", "task_list", {"filter": "overdue"}),
    ("Summarize my outstanding work.", "task_list", {"filter": "pending"}),
    ("Plan my next six hours.", "task_plan", {"hours": 6.0}),
    ("plan my next 4 hours", "task_plan", {"hours": 4.0}),
    ("Mark the installation task as complete.", "task_complete", {"match": "installation"}),
    ("ZANE, add a high-priority task to finish the robotics setup tomorrow.", "task_add",
     {"title": "Finish the robotics setup", "priority": "high", "due": "tomorrow"}),
    ("add a task to buy milk", "task_add", {"title": "Buy milk", "priority": "normal"}),
    ("add a critical task to ship the v1 tomorrow", "task_add", {"title": "Ship the v1", "priority": "critical", "due": "tomorrow"}),
    ("create a low priority task to sort photos on friday", "task_add", {"title": "Sort photos", "priority": "low", "due": "friday"}),
    ("remember that I prefer concise answers", "memory_save", {"content": "I prefer concise answers", "kind": "preference"}),
    ("What do you remember about robotics?", "memory_search", {"query": "robotics"}),
])
def test_routes(text, tool, args):
    route = R.match(text)
    assert route is not None and route.tool == tool
    assert {k: route.args[k] for k in args} == args


@pytest.mark.parametrize("text", [
    "ZANE, open my robotics project and check its status.", "Open this project in the editor.", "open it", "open the file report.txt",
    "ZANE, I need to finish three projects today.", "Can you fix this error?", "why is my battery draining so fast",
    "how do I free up storage space", "tell me a joke", "what's the weather", "", "   ", "x" * 300,
    "add a task", "add a critical task", "delete everything in my Downloads", "run sudo rm -rf /", "forget the saved location of that project",
])
def test_ambiguous_or_dangerous_text_goes_to_the_model_not_a_tool(text):
    assert R.match(text) is None
