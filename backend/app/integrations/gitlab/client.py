"""Typed async wrapper around the GitLab REST API.

The client deliberately exposes only the small surface the review MVP
needs today:

* fetch MR changes/diff
* post line-level MR discussions
* post an MR note
* set a commit status

Keeping the wrapper narrow avoids leaking raw HTTP concerns into the
review orchestrator while leaving room for richer Discussion APIs in a
later PR.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

CommitStatusState = Literal["pending", "running", "success", "failed", "canceled", "skipped"]


class GitLabClientError(RuntimeError):
    """Raised when GitLab returns a non-successful HTTP response."""

    def __init__(self, *, status_code: int, message: str, response_body: str) -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"GitLab API error {status_code}: {message}")


class GitLabClient:
    """Small async GitLab API client used by the review pipeline.

    Args:
        base_url: GitLab instance URL, e.g. ``https://gitlab.example.com``.
        token: Personal/project access token with MR read/write access.
        timeout_seconds: Per-request timeout.
    """

    def __init__(self, *, base_url: str, token: str, timeout_seconds: float = 15.0) -> None:
        if not base_url:
            msg = "GitLab base_url must not be empty."
            raise ValueError(msg)
        if not token:
            msg = "GitLab token must not be empty."
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds

    async def get_merge_request_changes(self, *, project_id: int, mr_iid: int) -> dict[str, Any]:
        """Fetch raw GitLab MR changes payload.

        Args:
            project_id: Numeric GitLab project ID.
            mr_iid: MR IID scoped to the project.

        Returns:
            dict[str, Any]: Raw GitLab response with a ``changes`` array.
        """

        return await self._request_json(
            "GET",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}/changes",
        )

    async def create_merge_request_note(
        self,
        *,
        project_id: int,
        mr_iid: int,
        body: str,
    ) -> dict[str, Any]:
        """Create a top-level note on an MR.

        Args:
            project_id: Numeric GitLab project ID.
            mr_iid: MR IID scoped to the project.
            body: Markdown comment body.

        Returns:
            dict[str, Any]: Raw GitLab note response.
        """

        if not body.strip():
            msg = "GitLab note body must not be empty."
            raise ValueError(msg)
        return await self._request_json(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )

    async def create_merge_request_discussion(
        self,
        *,
        project_id: int,
        mr_iid: int,
        body: str,
        base_sha: str,
        start_sha: str,
        head_sha: str,
        old_path: str,
        new_path: str,
        line_number: int,
    ) -> dict[str, Any]:
        """Create a line-level discussion on an MR diff.

        Args:
            project_id: Numeric GitLab project ID.
            mr_iid: MR IID scoped to the project.
            body: Markdown discussion body.
            base_sha: Base SHA from GitLab MR diff refs.
            start_sha: Start SHA from GitLab MR diff refs.
            head_sha: Head SHA from GitLab MR diff refs.
            old_path: Old-path file location for the finding.
            new_path: New-path file location for the finding.
            line_number: New-file line number for the finding.

        Returns:
            dict[str, Any]: Raw GitLab discussion response.
        """

        if not body.strip():
            msg = "GitLab discussion body must not be empty."
            raise ValueError(msg)
        if not base_sha.strip():
            msg = "GitLab discussion base_sha must not be empty."
            raise ValueError(msg)
        if not start_sha.strip():
            msg = "GitLab discussion start_sha must not be empty."
            raise ValueError(msg)
        if not head_sha.strip():
            msg = "GitLab discussion head_sha must not be empty."
            raise ValueError(msg)
        if not old_path.strip():
            msg = "GitLab discussion old_path must not be empty."
            raise ValueError(msg)
        if not new_path.strip():
            msg = "GitLab discussion new_path must not be empty."
            raise ValueError(msg)
        if line_number <= 0:
            msg = "GitLab discussion line_number must be positive."
            raise ValueError(msg)
        return await self._request_json(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            json={
                "body": body,
                "position": {
                    "position_type": "text",
                    "base_sha": base_sha,
                    "start_sha": start_sha,
                    "head_sha": head_sha,
                    "old_path": old_path,
                    "new_path": new_path,
                    "new_line": line_number,
                },
            },
        )

    async def set_commit_status(
        self,
        *,
        project_id: int,
        commit_sha: str,
        state: CommitStatusState,
        name: str,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """Set a GitLab commit status for the reviewed commit.

        Args:
            project_id: Numeric GitLab project ID.
            commit_sha: Commit SHA to mark.
            state: GitLab status state.
            name: Status context name.
            description: Human-readable status description.
            target_url: Optional link to the review detail page.

        Returns:
            dict[str, Any]: Raw GitLab status response.
        """

        payload: dict[str, Any] = {
            "state": state,
            "name": name,
            "description": description,
        }
        if target_url:
            payload["target_url"] = target_url
        return await self._request_json(
            "POST",
            f"/api/v4/projects/{project_id}/statuses/{commit_sha}",
            json=payload,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request and return parsed JSON.

        Raises:
            GitLabClientError: On any non-2xx response.
        """

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"PRIVATE-TOKEN": self._token},
            timeout=self._timeout,
        ) as client:
            response = await client.request(method, path, json=json)

        if response.status_code < 200 or response.status_code >= 300:
            raise GitLabClientError(
                status_code=response.status_code,
                message=self._extract_error_message(response),
                response_body=response.text,
            )
        data = response.json()
        if not isinstance(data, dict):
            return {"data": data}
        return data

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """Best-effort extraction of GitLab error messages."""

        try:
            data = response.json()
        except ValueError:
            return response.text
        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return "; ".join(f"{k}: {v}" for k, v in message.items())
        return response.text
