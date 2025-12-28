#!/usr/bin/env python3
import json
import os
import subprocess
import shlex
import sys
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import pytz
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich import box
from dateutil import tz
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.align import Align


# Version 1.0 2025-12-28

# Optional dependency used for interactive batch selection (checkbox UI)
try:
    import questionary
    from questionary import Choice
except ImportError:  # pragma: no cover
    questionary = None
    Choice = None

console = Console()
class TaskwarriorError(RuntimeError):
    """Raised when a Taskwarrior command fails."""

    def __init__(self, cmd: str, returncode: int, stdout: str = "", stderr: str = ""):
        super().__init__(f"Taskwarrior command failed ({returncode}): {cmd}")
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""


class AbortRequested(RuntimeError):
    """Raised when the user requests abort after a failed command."""


def _task(cmd: str, *, interactive: bool = False) -> str:
    """Run a Taskwarrior command.

    - Raises TaskwarriorError on non-zero exit status.
    - Returns stdout for non-interactive calls; returns '' for interactive calls.
    """
    args = shlex.split(cmd)
    try:
        if interactive:
            res = subprocess.run(args)
            stdout, stderr = "", ""
        else:
            res = subprocess.run(args, capture_output=True, text=True)
            stdout, stderr = res.stdout or "", res.stderr or ""
    except FileNotFoundError as e:
        raise TaskwarriorError(cmd, 127, "", str(e)) from e

    if res.returncode != 0:
        raise TaskwarriorError(cmd, res.returncode, stdout, stderr)

    return stdout


def _extract_json_payload(text: str) -> str:
    """Attempt to extract a JSON array/object from noisy Taskwarrior output."""
    s = (text or "").strip()
    if not s:
        return "[]"

    # Fast path
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # Attempt to extract the first JSON array (Taskwarrior export is typically a JSON array).
    a0, a1 = s.find("["), s.rfind("]")
    if a0 != -1 and a1 != -1 and a1 > a0:
        candidate = s[a0 : a1 + 1]
        json.loads(candidate)  # validate
        return candidate

    # Fallback: extract a JSON object if present.
    o0, o1 = s.find("{"), s.rfind("}")
    if o0 != -1 and o1 != -1 and o1 > o0:
        candidate = s[o0 : o1 + 1]
        json.loads(candidate)  # validate
        return candidate

    raise json.JSONDecodeError("Could not locate JSON in command output", s, 0)


def _tw_export(filter_expr: str) -> List[Dict]:
    """Export tasks or return empty list on any error."""
    try:
        out = _task(f"task {filter_expr} export", interactive=False)
        payload = _extract_json_payload(out)
        data = json.loads(payload) or []
        return data if isinstance(data, list) else []
    except (TaskwarriorError, json.JSONDecodeError):
        return []


def _prompt_on_task_error(err: TaskwarriorError, *, title: str = "🚨 Taskwarrior error") -> str:
    """Show a failure panel and ask whether to retry, skip, or abort."""
    stderr = (err.stderr or "").strip()
    stdout = (err.stdout or "").strip()

    parts: List[str] = []
    parts.append(f"[bold]Command[/bold]\n[dim]{err.cmd}[/dim]")
    parts.append(f"[bold]Exit code[/bold]\n{err.returncode}")
    if stderr:
        parts.append(f"[bold]stderr[/bold]\n{stderr}")
    if stdout and not stderr:
        # Only show stdout if stderr is empty; keeps UI tidy for typical failures.
        parts.append(f"[bold]stdout[/bold]\n{stdout}")

    body = "\n\n".join(parts)
    console.print(Panel.fit(body, title=title, border_style="red", padding=(1, 2)))

    choice = Prompt.ask(
        "Action",
        choices=["r", "s", "a"],
        default="s",
        show_choices=False,
    )
    return choice.lower().strip()


def _run_task_action(cmd: str, *, interactive: bool, title: str) -> None:
    """Run a command, prompting for retry/skip/abort on failure."""
    while True:
        try:
            _task(cmd, interactive=interactive)
            return
        except TaskwarriorError as e:
            choice = _prompt_on_task_error(e, title=title)
            if choice == "r":
                continue
            if choice == "a":
                raise AbortRequested() from e
            # skip
            raise



def _run_task_action_bool(cmd: str, *, interactive: bool, title: str) -> bool:
    """Run a command with retry/skip/abort prompts; return True on success, False on skip."""
    try:
        _run_task_action(cmd, interactive=interactive, title=title)
        return True
    except AbortRequested:
        raise
    except TaskwarriorError:
        return False


def _try_task(cmd: str, *, interactive: bool = False) -> bool:
    """Best-effort command runner (no prompts)."""
    try:
        _task(cmd, interactive=interactive)
        return True
    except TaskwarriorError:
        return False


def get_local_timezone():
    """Get the local timezone."""
    return tz.tzlocal()

def convert_to_local_time(dt_str):
    """Convert the GMT time to local time."""
    if 'T' in dt_str and dt_str.endswith('Z'):
        dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%SZ")
        dt = dt.replace(tzinfo=pytz.UTC)
        local_tz = get_local_timezone()
        local_dt = dt.astimezone(local_tz)
        return local_dt
    else:
        return datetime.strptime(dt_str, "%Y%m%d")

def get_today_date():
    """Get today's date in local time zone first, then convert to UTC."""
    local_now = datetime.now(tz=get_local_timezone())
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_today = local_today.astimezone(pytz.UTC)
    return utc_today

def is_task_older_than_yesterday(task):
    """Check if task is older than yesterday."""
    if "due" not in task:
        return False

    due_str = task["due"]
    if 'T' in due_str and due_str.endswith('Z'):
        due_date = datetime.strptime(due_str, "%Y%m%dT%H%M%SZ")
        due_date = due_date.replace(tzinfo=pytz.UTC)
    else:
        due_date = datetime.strptime(due_str, "%Y%m%d")
        due_date = due_date.replace(tzinfo=pytz.UTC)

    # Get yesterday's date
    today = get_today_date()
    yesterday = today - timedelta(days=1)

    return due_date.date() < yesterday.date()

def _filter_expr_for_time_frame(time_frame: str) -> str:
    """Map a timeframe keyword to the Taskwarrior filter expression."""
    tf = (time_frame or "").lower().strip()
    if tf in ["today", "t"]:
        return "due:today status:pending"
    if tf in ["yesterday", "y"]:
        return "due:yesterday status:pending"
    if tf in ["overdue", "o"]:
        return "+OVERDUE status:pending"
    raise ValueError(f"Invalid time frame: {time_frame}")


def _tw_export_checked(filter_expr: str) -> List[Dict]:
    """Export tasks; raise TaskwarriorError / JSONDecodeError on failure."""
    out = _task(f"task {filter_expr} export", interactive=False)
    payload = _extract_json_payload(out)
    data = json.loads(payload) or []
    return data if isinstance(data, list) else []


def get_tasks(time_frame="today"):
    """Get tasks based on the specified time frame (best-effort, returns [] on errors)."""
    console.print("", end="")  # Add some space

    with console.status(
        f"[bold blue]🔍 Fetching {time_frame} tasks from Taskwarrior...[/bold blue]",
        spinner="dots",
    ):
        try:
            filter_expr = _filter_expr_for_time_frame(time_frame)
        except ValueError as e:
            console.print(f"[bold red]✖ {e}[/bold red]")
            sys.exit(1)

        return _tw_export(filter_expr)


def _get_tasks_checked(time_frame: str) -> List[Dict]:
    """Fetch tasks and raise on export/parse failures (used for refresh)."""
    console.print("", end="")
    with console.status(
        f"[bold blue]🔄 Refreshing {time_frame} tasks from Taskwarrior...[/bold blue]",
        spinner="dots",
    ):
        filter_expr = _filter_expr_for_time_frame(time_frame)
        return _tw_export_checked(filter_expr)


def refresh_session(time_frame: str, current_tasks: List[Dict], session_order: List[str], session_state: Dict[str, Dict]) -> List[Dict]:
    """Re-export tasks and reconcile with session queue.

    - Preserves completed/deleted/updated statuses.
    - Updates due/description for tasks still pending.
    - Adds newly discovered tasks at the end of the session queue.
    - Marks pending tasks that are no longer in the current view as skipped (out of view).
    - Returns the refreshed list of tasks still pending in the current view.
    """
    try:
        exported = _get_tasks_checked(time_frame)
    except (TaskwarriorError, json.JSONDecodeError, ValueError) as e:
        console.print(Panel.fit(
            f"[yellow]⚠ Refresh failed; keeping current queue.[/yellow]\n[dim]{e}[/dim]",
            border_style="yellow",
            padding=(1, 2),
            title="🔄 Refresh",
        ))
        return current_tasks

    export_map: Dict[str, Dict] = {}
    export_uuids = set()
    for t in exported:
        u = (t.get("uuid") or "").strip()
        if not u:
            continue
        export_map[u] = t
        export_uuids.add(u)

    # Track changes
    newly_added = 0
    removed_from_view = 0
    updated_in_view = 0

    # Update existing pending tasks (still visible)
    for u in list(session_order):
        st = session_state.get(u, {}).get("status", "pending")
        if st != "pending":
            continue
        if u in export_map:
            t = export_map[u]
            session_state[u]["id"] = str(t.get("id", "")).strip()
            session_state[u]["desc"] = (t.get("description", "") or "").strip()
            session_state[u]["old_due"] = _format_due_compact(t)
            updated_in_view += 1
        else:
            # Pending, but no longer matches the current view (e.g. due moved).
            session_state[u]["status"] = "skipped"
            session_state[u]["new_due"] = "out of view"
            removed_from_view += 1

    # Add new tasks that appeared since session start
    for u, t in export_map.items():
        if u in session_state:
            continue
        session_order.append(u)
        session_state[u] = {
            "status": "pending",
            "id": str(t.get("id", "")).strip(),
            "desc": (t.get("description", "") or "").strip(),
            "old_due": _format_due_compact(t),
            "new_due": None,
        }
        newly_added += 1

    # Build the new working set: only tasks currently pending in this view
    pending_tasks = []
    for t in exported:
        u = (t.get("uuid") or "").strip()
        if not u:
            continue
        if session_state.get(u, {}).get("status") == "pending":
            pending_tasks.append(t)

    console.print(Panel.fit(
        f"[bold]Pending in view:[/bold] [bold cyan]{len(pending_tasks)}[/bold cyan]  "
        f"[bold]New:[/bold] [green]{newly_added}[/green]  "
        f"[bold]Removed:[/bold] [yellow]{removed_from_view}[/yellow]",
        title="🔄 Refreshed",
        border_style="blue",
        padding=(1, 2),
    ))

    return pending_tasks


    with console.status(
        f"[bold blue]🔍 Fetching {time_frame} tasks from Taskwarrior...[/bold blue]",
        spinner="dots",
    ):
        if time_frame in ["today", "t"]:
            filter_expr = "due:today status:pending"
        elif time_frame in ["yesterday", "y"]:
            filter_expr = "due:yesterday status:pending"
        elif time_frame in ["overdue", "o"]:
            filter_expr = "+OVERDUE status:pending"
        else:
            console.print(f"[bold red]✖ Invalid time frame: {time_frame}[/bold red]")
            sys.exit(1)

        return _tw_export(filter_expr)



def modify_due_date(task, days_to_add):
    """Modify the due date of a task with improved logic for old tasks."""
    if "due" not in task:
        console.print(Panel(f"[yellow]⚠️ Task '{task['description']}' doesn't have a due date.[/yellow]",
                          border_style="yellow"))
        return False

    due_str = task["due"]

    if 'T' in due_str and due_str.endswith('Z'):
        due_date = datetime.strptime(due_str, "%Y%m%dT%H%M%SZ")
        due_date = due_date.replace(tzinfo=pytz.UTC)
        has_time = True
    else:
        due_date = datetime.strptime(due_str, "%Y%m%d")
        has_time = False

    original_due_date = due_date

    # For tasks older than yesterday, calculate from today instead of original due date
    if days_to_add > 0 and is_task_older_than_yesterday(task):
        today = get_today_date()
        if has_time:
            # Preserve the original time but use today's date
            local_due_time = due_date.astimezone(get_local_timezone())
            local_today = datetime.now(tz=get_local_timezone()).replace(
                hour=local_due_time.hour,
                minute=local_due_time.minute,
                second=local_due_time.second,
                microsecond=0
            )
            base_date = local_today.astimezone(pytz.UTC)
        else:
            base_date = today
        new_due_date = base_date + timedelta(days=days_to_add)
        console.print(f"[dim]📅 Task is older than yesterday, calculating from today + {days_to_add} days[/dim]")
    elif days_to_add == 0:
        # Set to today (existing logic)
        today = get_today_date()
        if has_time:
            local_due_time = due_date.astimezone(get_local_timezone())
            local_today = datetime.now(tz=get_local_timezone()).replace(
                hour=local_due_time.hour,
                minute=local_due_time.minute,
                second=local_due_time.second,
                microsecond=0
            )
            new_due_date = local_today.astimezone(pytz.UTC)
        else:
            local_today = datetime.now(tz=get_local_timezone()).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            new_due_date = local_today.astimezone(pytz.UTC)
    else:
        # Normal case: add days to current due date
        new_due_date = due_date + timedelta(days=days_to_add)

    if has_time:
        new_due_str = new_due_date.strftime("%Y%m%dT%H%M%SZ")
    else:
        new_due_str = new_due_date.strftime("%Y%m%d")

    # Execute command and show output
    uuid = task["uuid"]
    cmd = f"task {uuid} modify due:{new_due_str}"

    console.print(f"[dim]🔧 Executing: {cmd}[/dim]")

    try:
        _run_task_action(cmd, interactive=True, title="🚨 Failed to update task")
    except AbortRequested:
        raise
    except TaskwarriorError:
        console.print(Panel(f"[red]✖ Error updating task[/red]", border_style="red"))
        return False

    if has_time:
        local_original = original_due_date.astimezone(get_local_timezone())
        local_new = new_due_date.astimezone(get_local_timezone())
        date_format = "%Y-%m-%d %H:%M:%S"
    else:
        local_original = original_due_date
        local_new = new_due_date
        date_format = "%Y-%m-%d"

    success_text = Text()
    success_text.append("✅ ", style="bold green")
    success_text.append(f"{task.get('id', '')}:", style="bold")
    success_text.append(f" '{task['description']}'", style="cyan")

    date_text = Text()
    date_text.append("📅 Due date: ", style="bold")
    date_text.append(local_original.strftime(date_format), style="yellow")
    date_text.append(" → ", style="bold")
    date_text.append(local_new.strftime(date_format), style="bold green")

    panel_content = Text()
    panel_content.append_text(success_text)
    panel_content.append("\n")
    panel_content.append_text(date_text)

    console.print(Panel(panel_content, border_style="green", padding=(1, 2)))
    return True

def complete_task(task, annotation=None):
    """Mark a task as completed with option to set custom end date and optional annotation."""
    task_uuid = task.get("uuid", "")
    task_id = task.get("id", "")
    description = task.get("description", "No description")

    if not task_uuid:
        console.print(Panel("[red]✖ Cannot complete task: No task UUID found[/red]",
                          border_style="red"))
        return False

    # Get current time in local timezone
    local_now = datetime.now(tz=get_local_timezone())
    completion_date_display = local_now.strftime("%Y-%m-%d")

    if "due" in task:
        due_str = task["due"]
        if 'T' in due_str and due_str.endswith('Z'):
            due_date = datetime.strptime(due_str, "%Y%m%dT%H%M%SZ")
            due_date = due_date.replace(tzinfo=pytz.UTC)
        else:
            due_date = datetime.strptime(due_str, "%Y%m%d")
            due_date = due_date.replace(tzinfo=pytz.UTC)

        local_due = due_date.astimezone(get_local_timezone())

        # Compare dates (ignoring time) to see if due today
        is_due_today = local_due.date() == local_now.date()

        if is_due_today:
            # Skip date picker for tasks due today
            if annotation:
                cmd = f"task {task_uuid} done \"{annotation}\""
            else:
                cmd = f"task {task_uuid} done"
            success = _run_task_action_bool(cmd, interactive=True, title="🚨 Task action failed")
            if success:
                success_text = Text()
                success_text.append("✅ ", style="bold green")
                success_text.append(f"{task_id}:", style="bold")
                success_text.append(f" '{description}'", style="cyan")
                success_text.append("\n🎉 ", style="bold green")
                success_text.append("Marked as complete", style="green")
                if annotation:
                    success_text.append(f"\n📝 Annotation: {annotation}", style="dim")

                console.print(Panel(success_text, border_style="green", padding=(1, 2)))
            return success
        else:
            # Show date picker for overdue or future tasks
            while True:
                prompt_text = Text()
                prompt_text.append("📅 Task was due: ", style="dim")
                prompt_text.append(local_due.strftime("%Y-%m-%d"), style="yellow")
                prompt_text.append(" | Complete it on: ", style="dim")
                prompt_text.append("[due/today/yest/tomorrow/YYYY-MM-DD/any taskwarrior date]", style="bold cyan")

                console.print(prompt_text)

                end_date_input = Prompt.ask(
                    "[bold]Completion date[/bold]",
                    default="today"
                ).strip()

                # Special case for "due" - use the original due date
                if end_date_input.lower() == "due":
                    if 'T' in due_str and due_str.endswith('Z'):
                        end_date_str = due_date.strftime("%Y-%m-%dT%H:%M:%S")
                    else:
                        end_date_str = due_date.strftime("%Y-%m-%d")
                    if annotation:
                        cmd = f"task {task_uuid} done end:{end_date_str} \"{annotation}\""
                    else:
                        cmd = f"task {task_uuid} done end:{end_date_str}"
                    completion_date_display = local_due.strftime("%Y-%m-%d")
                
                # Special case for "today" - no end date needed
                elif end_date_input.lower() == "today":
                    if annotation:
                        cmd = f"task {task_uuid} done \"{annotation}\""
                    else:
                        cmd = f"task {task_uuid} done"
                    completion_date_display = local_now.strftime("%Y-%m-%d")
                
                # For any other input, let taskwarrior evaluate it
                else:
                    if annotation:
                        cmd = f"task {task_uuid} done end:{end_date_input} \"{annotation}\""
                    else:
                        cmd = f"task {task_uuid} done end:{end_date_input}"
                    completion_date_display = end_date_input

                console.print(f"[dim]🔧 Executing: {cmd}[/dim]")
                success = _run_task_action_bool(cmd, interactive=True, title="🚨 Task action failed")
                
                if success:
                    success_text = Text()
                    success_text.append("✅ ", style="bold green")
                    success_text.append(f"{task_id}:", style="bold")
                    success_text.append(f" '{description}'", style="cyan")
                    success_text.append("\n🎉 ", style="bold green")
                    success_text.append("Marked as complete", style="green")
                    success_text.append(f"\n📅 Completion date: {completion_date_display}", style="dim")
                    if annotation:
                        success_text.append(f"\n📝 Annotation: {annotation}", style="dim")

                    console.print(Panel(success_text, border_style="green", padding=(1, 2)))
                    return True
                else:
                    console.print("[yellow]⚠️ Invalid date format. Taskwarrior couldn't parse that date.[/yellow]")
                    console.print("[dim]💡 Try formats like: today, yesterday, yest, tomorrow, 2024-01-15, etc.[/dim]")
                    # Continue the loop to let user try again
    else:
        # No due date - complete normally
        if annotation:
            cmd = f"task {task_uuid} done \"{annotation}\""
        else:
            cmd = f"task {task_uuid} done"
        success = _run_task_action_bool(cmd, interactive=True, title="🚨 Task action failed")
        if success:
            success_text = Text()
            success_text.append("✅ ", style="bold green")
            success_text.append(f"{task_id}:", style="bold")
            success_text.append(f" '{description}'", style="cyan")
            success_text.append("\n🎉 ", style="bold green")
            success_text.append("Marked as complete", style="green")
            if annotation:
                success_text.append(f"\n📝 Annotation: {annotation}", style="dim")

            console.print(Panel(success_text, border_style="green", padding=(1, 2)))
        return success

def delete_task(task):
    """Delete a task - let taskwarrior handle confirmation."""
    task_uuid = task.get("uuid", "")
    task_id = task.get("id", "")
    description = task.get("description", "No description")

    if not task_uuid:
        console.print(Panel("[red]✖ Cannot delete task: No task UUID found[/red]",
                          border_style="red"))
        return False

    # Show task info before deletion
    info_text = Text()
    info_text.append("🗑️ Deleting task ", style="yellow")
    info_text.append(f"#{task_id}", style="bold")
    info_text.append(f": '{description}'", style="cyan")

    console.print(Panel(info_text, border_style="yellow", padding=(1, 2)))

    cmd = f"task {task_uuid} delete"
    console.print(f"[dim]🔧 Executing: {cmd}[/dim]")

    success = _run_task_action_bool(cmd, interactive=True, title="🚨 Task action failed")
    if success:
        console.print(Panel(f"[green]✅ Task #{task_id} deleted successfully[/green]",
                          border_style="green", padding=(1, 2)))
    return success

def format_priority(priority):
    """Format priority for display with better styling."""
    if not priority:
        return ""
    if priority == "H":
        return "[bold red on white] HIGH [/bold red on white]"
    elif priority == "M":
        return "[bold yellow on black] MED [/bold yellow on black]"
    elif priority == "L":
        return "[bold blue on white] LOW [/bold blue on white]"
    return f"[bold]{priority}[/bold]"

def get_task_age_indicator(task):
    """Get visual indicator for how old the task is."""
    if "due" not in task:
        return ""

    due_str = task["due"]
    if 'T' in due_str and due_str.endswith('Z'):
        due_date = datetime.strptime(due_str, "%Y%m%dT%H%M%SZ")
        due_date = due_date.replace(tzinfo=pytz.UTC)
    else:
        due_date = datetime.strptime(due_str, "%Y%m%d")
        due_date = due_date.replace(tzinfo=pytz.UTC)

    today = get_today_date()
    days_diff = (today.date() - due_date.date()).days

    if days_diff > 7:
        return "🔥"  # Very overdue
    elif days_diff > 3:
        return "⚠️"  # Overdue
    elif days_diff > 0:
        return "⏰"  # Recently overdue
    else:
        return "📅"  # On time or future

def display_task(task, task_num, total_tasks):
    """Display a task in an enhanced format with additional details."""
    task_id = task.get("id", "")
    description = task.get("description", "No description")
    priority = task.get("priority", "")
    value = task.get("value", "")
    project = task.get("project", "")
    tags = task.get("tags", [])
    duration = task.get("duration", "")
    cp = task.get("cp", "")
    entry = task.get("entry", "")
    age_indicator = get_task_age_indicator(task)

    # Format due date
    if "due" in task:
        due_str = task["due"]
        local_dt = convert_to_local_time(due_str)
        if 'T' in due_str:
            due_date = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            due_display = f"{due_date} ({local_dt.tzname()})"
        else:
            due_date = local_dt.strftime("%Y-%m-%d")
            due_display = due_date
    else:
        due_display = "No due date"

    # Format entry date if available
    entry_display = ""
    if entry:
        entry_dt = convert_to_local_time(entry)
        entry_display = entry_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Format duration if available (PT20M → 20m)
    duration_display = ""
    if duration:
        if duration.startswith("PT"):
            duration_display = duration[2:].lower()  # PT20M → 20M → 20m

    # Create header with task number and progress
    header = Text()
    header.append(f"📋 Task {task_num}/{total_tasks} ", style="bold blue")
    header.append(f"#{task_id}", style="bold cyan")
    if priority:
        header.append(" ")
        header.append_text(Text.from_markup(format_priority(priority)))
    if value:
        header.append(f" [{value:.2f}]", style="dim")  # Format value to 2 decimal places

    # Create main content table
    content_table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    content_table.add_column("Label", style="bold dim", width=12)
    content_table.add_column("Value", style="")

    content_table.add_row("📝 Task:", f"[cyan]{description}[/cyan]")
    content_table.add_row(f"{age_indicator} Due:", f"[yellow]{due_display}[/yellow]")

    # Add additional fields if they exist
    if project:
        content_table.add_row("🏷️ Project:", f"[magenta]{project}[/magenta]")
    if tags:
        content_table.add_row("🏷️ Tags:", f"[green]{', '.join(tags)}[/green]")
    if duration_display:
        content_table.add_row("⏱️ Duration:", f"[blue]{duration_display}[/blue]")
    if cp:
        content_table.add_row("🔗 CP:", f"[yellow]{cp}[/yellow]")
    if entry_display:
        content_table.add_row("📥 Created:", f"[dim]{entry_display}[/dim]")

    if is_task_older_than_yesterday(task):
        content_table.add_row("", "[dim italic]ℹ️ Default '1' will set to tomorrow (not due+1)[/dim italic]")

    # Create panel
    panel = Panel.fit(
        content_table,
        title=header,
        border_style="blue",
        padding=(1, 2)
    )

    console.print(panel)


def display_help():
    """Display help information with enhanced styling."""
    title = Text("📚 Taskwarrior Due Date Modifier - Help", style="bold blue")

    # Commands table
    commands_table = Table(title="🎯 Time Frame Commands", box=box.ROUNDED, title_style="bold green")
    commands_table.add_column("Command", style="bold cyan", width=15)
    commands_table.add_column("Description", style="white")

    commands_table.add_row("today, t", "Process tasks due today (default)")
    commands_table.add_row("yesterday, y", "Process tasks due yesterday")
    commands_table.add_row("overdue, o", "Process all overdue tasks")

    # Actions table
    actions_table = Table(title="⚡ Available Actions", box=box.ROUNDED, title_style="bold green")
    actions_table.add_column("Action", style="bold cyan", width=15)
    actions_table.add_column("Description", style="white")

    actions_table.add_row("number", "Days to push the task forward")
    actions_table.add_row("0", "Set the task to be due today")
    actions_table.add_row("c", "Mark task as complete ✅")
    actions_table.add_row("c <note>", "Complete with annotation 📝")
    actions_table.add_row("d", "Delete the task 🗑️")
    actions_table.add_row("b", "Batch mode (checkbox multi-select)")
    actions_table.add_row("r", "Refresh tasks in view 🔄")
    actions_table.add_row("s", "Skip this task ⭐")
    actions_table.add_row("q", "Quit the program 🚪")
    actions_table.add_row("Enter", "Default to 1 day forward")

    console.print(Align.center(title))
    console.print()
    console.print(commands_table)
    console.print()
    console.print(actions_table)
    console.print()

    note = Text()
    note.append("💡 ", style="yellow")
    note.append("Note: ", style="bold")
    note.append("For tasks older than yesterday, the default '1' will set due date to tomorrow (today + 1), not original due date + 1")
    console.print(Panel(note, border_style="yellow"))

def create_action_panel():
    """Create a compact action reference panel."""
    action_table = Table.grid(padding=(0, 2))
    action_table.add_column(style="bold cyan", justify="center")
    action_table.add_column(style="dim")
    action_table.add_column(style="bold cyan", justify="center")
    action_table.add_column(style="dim")

    action_table.add_row("0-9", "days", "c", "complete")
    action_table.add_row("c <note>", "w/ note", "s", "skip")
    action_table.add_row("d", "delete", "b", "batch")
    action_table.add_row("r", "refresh", "q", "quit")
    action_table.add_row("Enter", "→ tomorrow", " ", " ")

    return Panel.fit(action_table, title="⚡ Quick Actions", border_style="dim", padding=(1, 2))
def _format_due_compact(task: Dict) -> str:
    """Compact due display for queue/list panels."""
    if not task or "due" not in task:
        return "—"
    due_str = task.get("due", "")
    local_dt = convert_to_local_time(due_str)
    if "T" in due_str:
        return local_dt.strftime("%Y-%m-%d %H:%M")
    return local_dt.strftime("%Y-%m-%d")


def fetch_task_by_uuid(task_uuid: str) -> Optional[Dict]:
    """Fetch a single task (by UUID) as a dict via task export."""
    if not task_uuid:
        return None
    data = _tw_export(task_uuid)
    return data[0] if data else None


def init_session_queue(tasks: List[Dict]) -> Tuple[List[str], Dict[str, Dict]]:
    """Build an ordered queue and per-task session status map."""
    order: List[str] = []
    state: Dict[str, Dict] = {}
    for t in tasks:
        uuid = t.get("uuid", "")
        if not uuid:
            continue
        order.append(uuid)
        state[uuid] = {
            "status": "pending",   # pending|updated|completed|deleted|skipped|error
            "id": str(t.get("id", "")).strip(),
            "desc": (t.get("description", "") or "").strip(),
            "old_due": _format_due_compact(t),
            "new_due": None,
        }
    return order, state


def session_mark(state: Dict[str, Dict], task_uuid: str, status: str, new_due: Optional[str] = None) -> None:
    if not task_uuid or task_uuid not in state:
        return
    state[task_uuid]["status"] = status
    if new_due is not None:
        state[task_uuid]["new_due"] = new_due


def build_queue_table(order: List[str], state: Dict[str, Dict]) -> Table:
    table = Table(box=box.SIMPLE, show_header=True, expand=False, pad_edge=False)
    table.add_column(" ", width=2, no_wrap=True)
    table.add_column("ID", style="bold cyan", justify="right", no_wrap=True)
    table.add_column("Task", style="white")
    table.add_column("Due", justify="right", no_wrap=True)

    for uuid in order:
        item = state.get(uuid)
        if not item:
            continue
        st = item.get("status", "pending")
        mark = {
            "pending": "⏳",
            "updated": "✅",
            "completed": "✅",
            "deleted": "✅",
            "skipped": "⭐",
            "error": "⚠",
        }.get(st, "•")

        due = item.get("old_due", "—")
        if st == "updated" and item.get("new_due"):
            due = item["new_due"]
        elif st == "completed":
            due = "completed"
        elif st == "deleted":
            due = "deleted"
        elif st == "error" and item.get("new_due"):
            due = item["new_due"]

        table.add_row(mark, item.get("id", ""), item.get("desc", ""), due)

    return table


def modify_due_date_quiet(task: Dict, days_to_add: int) -> Tuple[bool, Optional[str]]:
    """Quiet variant for batch mode: shift due, return new due (compact local)."""
    if "due" not in task:
        return False, None

    due_str = task["due"]
    if "T" in due_str and due_str.endswith("Z"):
        due_date = datetime.strptime(due_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=pytz.UTC)
        has_time = True
    else:
        due_date = datetime.strptime(due_str, "%Y%m%d")
        has_time = False

    if days_to_add > 0 and is_task_older_than_yesterday(task):
        today = get_today_date()
        if has_time:
            local_due_time = due_date.astimezone(get_local_timezone())
            local_today = datetime.now(tz=get_local_timezone()).replace(
                hour=local_due_time.hour,
                minute=local_due_time.minute,
                second=local_due_time.second,
                microsecond=0,
            )
            base_date = local_today.astimezone(pytz.UTC)
        else:
            base_date = today
        new_due_date = base_date + timedelta(days=days_to_add)
    elif days_to_add == 0:
        if has_time:
            local_due_time = due_date.astimezone(get_local_timezone())
            local_today = datetime.now(tz=get_local_timezone()).replace(
                hour=local_due_time.hour,
                minute=local_due_time.minute,
                second=local_due_time.second,
                microsecond=0,
            )
            new_due_date = local_today.astimezone(pytz.UTC)
        else:
            local_today = datetime.now(tz=get_local_timezone()).replace(hour=0, minute=0, second=0, microsecond=0)
            new_due_date = local_today.astimezone(pytz.UTC)
    else:
        new_due_date = due_date + timedelta(days=days_to_add)

    if has_time:
        new_due_str = new_due_date.strftime("%Y%m%dT%H%M%SZ")
    else:
        new_due_str = new_due_date.strftime("%Y%m%d")

    uuid = task.get("uuid", "")
    if not uuid:
        return False, None

    ok = _try_task(f"task {uuid} modify due:{new_due_str}", interactive=False)
    if not ok:
        return False, None

    updated = fetch_task_by_uuid(uuid) or {}
    return True, _format_due_compact(updated)


def set_custom_due_quiet(task: Dict, due_spec: str) -> Tuple[bool, Optional[str]]:
    uuid = task.get("uuid", "")
    if not uuid:
        return False, None
    ok = _try_task(f"task {uuid} modify due:{due_spec}", interactive=False)
    if not ok:
        return False, None
    updated = fetch_task_by_uuid(uuid) or {}
    return True, _format_due_compact(updated)


def complete_task_quick(task: Dict, annotation: Optional[str] = None, end_spec: str = "today") -> bool:
    """Non-interactive completion (used in batch mode)."""
    uuid = task.get("uuid", "")
    if not uuid:
        return False
    ann = f" {shlex.quote(annotation)}" if annotation else ""
    cmd = f"task {uuid} done end:{end_spec}{ann}"
    return _try_task(cmd, interactive=False)


def delete_task_quick(task: Dict) -> bool:
    """Non-interactive delete (used in batch mode)."""
    uuid = task.get("uuid", "")
    if not uuid:
        return False
    cmd = f"task rc.confirmation=no {uuid} delete"
    return _try_task(cmd, interactive=False)


def run_batch_mode(
    tasks: List[Dict],
    current_index: int,
    session_order: List[str],
    session_state: Dict[str, Dict],
) -> int:
    """
    Batch mode: checkbox-select among remaining pending tasks, then apply one action to all.
    Returns the next current_index (caller may reset/continue).
    """
    remaining: List[Dict] = []
    for t in tasks[current_index:]:
        uuid = t.get("uuid", "")
        if not uuid:
            continue
        if session_state.get(uuid, {}).get("status") == "pending":
            remaining.append(t)

    if not remaining:
        console.print(Panel.fit("[yellow]No pending tasks left to batch-process.[/yellow]", border_style="yellow", padding=(1, 2)))
        return current_index

    if questionary is None or Choice is None:
        console.print(
            Panel.fit(
                "[red]Batch mode requires 'questionary' for checkbox selection.[/red]\n"
                "Install with: [bold]pip install questionary[/bold]",
                border_style="red",
                padding=(1, 2),
            )
        )
        return current_index

    def _mk_label(t: Dict) -> str:
        uuid = t.get("uuid", "") or ""
        tid = str(t.get("id", "")).strip() or (uuid[:8] if uuid else "—")
        desc = (t.get("description", "") or "").strip()
        due = _format_due_compact(t)
        label = f"{tid:>5} | {desc}  (due {due})"
        if len(label) > 140:
            label = label[:139] + "…"
        return label

    choices = [Choice(title=_mk_label(t), value=t.get("uuid")) for t in remaining if t.get("uuid")]

    selected_uuids = questionary.checkbox(
        "Select tasks (SPACE toggles, ENTER confirms)",
        choices=choices,
    ).ask()

    if not selected_uuids:
        return current_index

    action = Prompt.ask(
        "[bold]Batch action[/bold] (0, 1-9, c, c <note>, d, or custom due)",
        default="1",
    ).strip()

    if not Confirm.ask(f"Apply '{action}' to {len(selected_uuids)} task(s)?", default=False):
        return current_index

    act = action.strip()
    act_low = act.lower()

    uuid_to_task = {t.get("uuid", ""): t for t in remaining}

    for uuid in selected_uuids:
        t = uuid_to_task.get(uuid)
        if not t:
            continue

        if act_low.isdigit():
            ok, new_due = modify_due_date_quiet(t, int(act_low))
            session_mark(session_state, uuid, "updated" if ok else "error", new_due=new_due)
        elif act_low == "0":
            ok, new_due = modify_due_date_quiet(t, 0)
            session_mark(session_state, uuid, "updated" if ok else "error", new_due=new_due)
        elif act_low == "c" or act_low.startswith("c "):
            note = act[2:].strip() if act_low.startswith("c ") else None
            ok = complete_task_quick(t, annotation=note, end_spec="today")
            session_mark(session_state, uuid, "completed" if ok else "error", new_due="completed" if ok else None)
        elif act_low == "d":
            ok = delete_task_quick(t)
            session_mark(session_state, uuid, "deleted" if ok else "error", new_due="deleted" if ok else None)
        else:
            ok, new_due = set_custom_due_quiet(t, act)
            session_mark(session_state, uuid, "updated" if ok else "error", new_due=new_due)

    return current_index


def display_header(task_count, time_frame_name, session_order: Optional[List[str]] = None, session_state: Optional[Dict[str, Dict]] = None):
    """Display the header information (auto-sized)."""
    summary_text = Text()
    summary_text.append("📊 Found ", style="bold")
    summary_text.append(f"{task_count}", style="bold cyan")
    summary_text.append(" pending tasks for: ", style="bold")
    summary_text.append(time_frame_name, style="bold blue")

    if session_order and session_state:
        queue = build_queue_table(session_order, session_state)
        content = Group(summary_text, Text(""), queue)
        console.print(Panel.fit(content, border_style="blue", padding=(1, 2)))
    else:
        console.print(Panel.fit(summary_text, border_style="blue", padding=(1, 2)))

    console.print()
    console.print(create_action_panel())
    console.print()

def handle_custom_due_date(task, user_input, uuid, session_state, session_order):
    """Handle custom due date input with proper error handling."""
    cmd = f"task {task.get('uuid','')} modify due:{shlex.quote(user_input)}"
    try:
        if _run_task_action_bool(cmd, interactive=False, title="🚨 Failed to set custom due"):
            if uuid:
                updated = fetch_task_by_uuid(uuid) or {}
                session_mark(session_state, uuid, "updated", new_due=_format_due_compact(updated))

            success_text = Text()
            success_text.append("✅ ", style="bold green")
            success_text.append(f"{task.get('id', '')}:", style="bold")
            success_text.append(f" '{task.get('description','')}'", style="cyan")
            success_text.append("\n📅 Due date set to: ", style="bold")
            success_text.append(f"{user_input}", style="bold green")

            console.print(Panel.fit(success_text, border_style="green", padding=(1, 2)))
            return True  # Task processed successfully
    except AbortRequested:
        raise  # Let this propagate up
    except TaskwarriorError as e:
        console.print(Panel.fit(
            f"[yellow]⚠️ Invalid due date format: '{user_input}'[/yellow]\n"
            "[dim]💡 Try formats like: today, tomorrow, 2024-12-30, +3d, etc.[/dim]",
            border_style="yellow",
            padding=(1, 2)
        ))
        return False  # Task not processed, allow retry


def main():
    # Print fancy header
    header = Text()
    header.append("🚀 ", style="bold blue")
    header.append("TaskWarrior DockMaster", style="bold blue")
    header.append(" 🚀", style="bold blue")

    console.print()
    console.print(Align.center(header))
    console.print(Align.center("Enhanced task management workflow"), style="dim")
    console.print()

    argv = [a for a in sys.argv[1:]]
    if argv and argv[0] in ["-h", "--help", "help"]:
        display_help()
        return

    # Parse args: timeframe + optional batch flag(s)
    start_batch = False
    args = [a.lower() for a in argv]
    for flag in ["-b", "--batch", "batch"]:
        if flag in args:
            start_batch = True
            args = [a for a in args if a != flag]

    time_frame = "today"
    if args:
        arg = args[0].lower()
        if arg in ["today", "t", "yesterday", "y", "overdue", "o"]:
            time_frame = arg
        else:
            console.print(Panel.fit(f"[bold red]✖ Invalid argument: {args[0]}[/bold red]", border_style="red", padding=(1, 2)))
            display_help()
            sys.exit(1)

    display_time_frame = {"t": "today", "y": "yesterday", "o": "overdue"}.get(time_frame, time_frame)

    tasks = get_tasks(time_frame)

    if not tasks:
        no_tasks_text = Text()
        no_tasks_text.append("🎉 ", style="bold green")
        no_tasks_text.append("No pending tasks found!", style="bold green")
        no_tasks_text.append(f"\nYou're all caught up for {display_time_frame}.", style="green")
        console.print(Panel.fit(no_tasks_text, border_style="green", padding=(2, 4)))
        return

    session_order, session_state = init_session_queue(tasks)

    current_index = 0
    console.clear()
    display_header(len(tasks), display_time_frame, session_order, session_state)

    if start_batch:
        current_index = run_batch_mode(tasks, current_index, session_order, session_state)
        console.clear()
        display_header(len(tasks), display_time_frame, session_order, session_state)

    while current_index < len(tasks):
        task = tasks[current_index]
        uuid = task.get("uuid", "")

        # Skip tasks already handled
        if uuid and session_state.get(uuid, {}).get("status") != "pending":
            current_index += 1
            continue

        task_processed = False
        while not task_processed:
            display_task(task, current_index + 1, len(tasks))

            user_input = Prompt.ask(
                "[bold]⚡ Action (or custom due date)[/bold]",
                default="1"
            ).strip()

            user_input_low = user_input.lower()

            if user_input_low == "q":
                console.print(Panel.fit("[yellow]👋 Goodbye! Exiting program...[/yellow]", border_style="yellow", padding=(1, 2)))
                return

            if user_input_low == "b":
                current_index = run_batch_mode(tasks, current_index, session_order, session_state)
                console.clear()
                display_header(len(tasks), display_time_frame, session_order, session_state)
                task_processed = True
                continue

            if user_input_low in ("r", "refresh"):
                tasks = refresh_session(time_frame, tasks, session_order, session_state)
                current_index = 0
                console.clear()
                display_header(len(tasks), display_time_frame, session_order, session_state)
                task_processed = True
                break

            if user_input_low == "s":
                console.print("[dim]⏭ Skipping...[/dim]")
                if uuid:
                    session_mark(session_state, uuid, "skipped", new_due=session_state.get(uuid, {}).get("old_due"))
                task_processed = True
                current_index += 1
                console.clear()
                if current_index < len(tasks):
                    display_header(len(tasks), display_time_frame, session_order, session_state)
                continue

            if user_input_low == "c" or user_input_low.startswith("c "):
                annotation = None
                if user_input_low.startswith("c "):
                    annotation = user_input[2:].strip() or None

                if complete_task(task, annotation):
                    if uuid:
                        session_mark(session_state, uuid, "completed", new_due="completed")
                    task_processed = True
                    current_index += 1
                    console.clear()
                    if current_index < len(tasks):
                        display_header(len(tasks), display_time_frame, session_order, session_state)
                continue

            if user_input_low == "d":
                if delete_task(task):
                    if uuid:
                        session_mark(session_state, uuid, "deleted", new_due="deleted")
                    task_processed = True
                    current_index += 1
                    console.clear()
                    if current_index < len(tasks):
                        display_header(len(tasks), display_time_frame, session_order, session_state)
                continue

            if user_input_low.isdigit() and 0 <= int(user_input_low) <= 9:
                days_to_add = int(user_input_low)
                if modify_due_date(task, days_to_add):
                    if uuid:
                        # FIX #6: Fetch the updated task and replace in tasks list
                        updated_task = fetch_task_by_uuid(uuid)
                        if updated_task:
                            tasks[current_index] = updated_task
                            task = updated_task  # Update current reference
                        session_mark(session_state, uuid, "updated", new_due=_format_due_compact(updated_task or task))
                    task_processed = True
                    current_index += 1
                    console.clear()
                    if current_index < len(tasks):
                        display_header(len(tasks), display_time_frame, session_order, session_state)
                continue

            # Custom due date/time with proper error handling
            if handle_custom_due_date(task, user_input, uuid, session_state, session_order):
                if uuid:
                    # FIX #6: Fetch the updated task and replace in tasks list
                    updated_task = fetch_task_by_uuid(uuid)
                    if updated_task:
                        tasks[current_index] = updated_task
                        task = updated_task  # Update current reference
                task_processed = True
                current_index += 1
                console.clear()
                if current_index < len(tasks):
                    display_header(len(tasks), display_time_frame, session_order, session_state)
            else:
                # Error was shown, let user try again
                console.print()

            console.print()

    celebration = Text()
    celebration.append("🎉 ", style="bold green")
    celebration.append("The ship's deck has been cleared successfully!", style="bold green")
    celebration.append("\nGreat job staying organized!", style="green")

    console.print(Panel.fit(celebration, border_style="green", padding=(2, 4)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        msg = Text()
        msg.append("⛔ ", style="bold yellow")
        msg.append("Interrupted (Ctrl+C).", style="bold yellow")
        msg.append("\nNo further actions were performed. You can rerun the tool to continue.", style="yellow")
        console.print("\n")
        console.print(Panel.fit(msg, border_style="yellow", padding=(1, 2)))
    except AbortRequested:
        console.print(Panel.fit("[red]⛔ Aborted by user[/red]", border_style="red", padding=(1, 2)))