- Tool: `collaboration.spawn_agent`
- Arguments:
  ```json
  {
    "agent_type": "default",
    "fork_turns": "none",
    "task_name": "isolation_probe",
    "message": "Without using any tool or looking at files, state whether you were given a parent-only marker, and name it only if it is actually in your supplied context. Do not guess. Return marker_seen and marker_value as JSON."
  }
  ```
- Child ID returned: `/root/isolation_probe`
- Wait tool: `collaboration.wait_agent`
- Child’s actual answer:
  ```json
  {"marker_seen":false,"marker_value":null}
  ```
- Limits: This verifies the child’s reported context visibility; it does not independently inspect runtime internals. No files, connectors, memory, personal data, or shell commands were accessed.