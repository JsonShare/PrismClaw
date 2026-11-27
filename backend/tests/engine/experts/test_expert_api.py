from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.engine.session_store import FakeRedisSessionStore

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.session_router import SessionRouter
from prismclaw.app.web import routes as web_routes
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.manifest import ExpertManifest
from prismclaw.engine.experts.registry.expert_registry import (
    ExpertRegistry,
    ExpertRegistryIssue,
)
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.schema.agent.session import SessionEvent, SessionEventType, SessionHeader
from prismclaw.schema.services.skills import SkillManifest

SESSION_ID = "web:tester:default:session-1"


def _expert_package(
    tmp_path: Path,
    name: str,
    *,
    expert_type: str = "agent",
    skills: tuple[str, ...] = (),
) -> ExpertPackage:
    root = tmp_path / "experts" / name
    (root / ".plugin").mkdir(parents=True)
    (root / "agents").mkdir()
    display_name = {"en": name, "zh": f"{name} 展示名"}
    members = []
    if expert_type == "team":
        members = [
            {
                "id": f"{name}-lead",
                "name": display_name,
                "displayName": display_name,
                "profession": display_name,
                "avatar": "avatars/lead.png",
                "role": "lead",
            }
        ]
    raw_manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} package description",
        "expertType": expert_type,
        "agentName": f"{name}-lead",
        "displayName": display_name,
        "profession": display_name,
        "displayDescription": (
            "用于验证专家列表与详情展示字段的完整中文描述，覆盖专业能力和输出边界。"
        ),
        "avatar": "avatars/lead.png",
        "categoryId": "test",
        "defaultInitPrompt": {"en": "Start", "zh": "开始"},
        "tags": ["test", "demo", "expert"],
        "quickPrompts": [
            {"en": "Start", "zh": "开始"},
            {"en": "Continue", "zh": "继续"},
            {"en": "Wrap up", "zh": "收尾"},
        ],
        "members": members,
    }
    (root / ".plugin" / "plugin.json").write_text(
        json.dumps(raw_manifest), encoding="utf-8"
    )
    (root / "agents" / f"{name}-lead.md").write_text(
        f"---\nname: {name}-lead\n---\n{name} persona prompt\n", encoding="utf-8"
    )
    return ExpertPackage(
        name=name,
        source="experts",
        path=str(root),
        root_path=root,
        manifest=ExpertManifest.model_validate(raw_manifest),
        skills=[
            SkillManifest(
                name=skill,
                name_zh=f"{skill} 中文名",
                description=f"{skill} description",
            )
            for skill in skills
        ],
    )


@pytest.fixture
def api_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, FakeRedisSessionStore]:
    writer = _expert_package(tmp_path, "writer", skills=("draft",))
    team = _expert_package(tmp_path, "clinical-team", expert_type="team")
    disabled = _expert_package(tmp_path, "disabled-expert", skills=("blocked",))
    research = _expert_package(
        tmp_path,
        "research",
        skills=("search", "disabled", *(f"skill-{index}" for index in range(21))),
    )
    experts = [writer, team, disabled, research]
    builtin = ExpertRegistry(
        experts,
        issues=[
            ExpertRegistryIssue(
                code="manifest_parse",
                expert_id="broken",
                message="broken failed to load",
            )
        ],
    )
    state_path = tmp_path / "capability_policy.yaml"
    state_path.write_text(
        "version: 1\n"
        "overrides:\n"
        "  expert:\n"
        "    disabled-expert: false\n"
        "  skill:\n"
        "    research:disabled: false\n",
        encoding="utf-8",
    )
    states = CapabilityPolicyRegistry(state_path, catalog=builtin)

    header = SessionHeader(
        id=SESSION_ID,
        session_key="web:tester:default",
        workspace_id="default",
        agent_id="agent-1",
        selected_skill_ids=["research:search"],
    )
    store = FakeRedisSessionStore()
    asyncio.run(
        store.commit_events(
            [
                SessionEvent(
                    seq=0,
                    type=SessionEventType.SESSION_CREATED,
                    session_id=SESSION_ID,
                    data={"type": "session_created", "header": header.model_dump()},
                )
            ]
        )
    )
    session_router = SessionRouter(store=store)
    monkeypatch.setattr(web_routes, "_store", store)
    monkeypatch.setattr(web_routes, "_session_router", session_router)

    app = FastAPI()
    app.state.web_deps = SimpleNamespace(
        session_store=store,
        session_router=session_router,
        runner_deps=SimpleNamespace(
            expert=SimpleNamespace(
                expert_registry=builtin,
                capability_policy_registry=states,
            )
        ),
    )
    app.dependency_overrides[get_current_user] = lambda: "tester"
    app.include_router(web_routes.web_router)
    return TestClient(app), store


def _error_code(response: object) -> str:
    return response.json()["detail"]["code"]  # type: ignore[attr-defined,no-any-return]


def test_expert_list_and_read_only_detail_include_display_fields(
    api_client: tuple[TestClient, FakeRedisSessionStore],
) -> None:
    client, _ = api_client

    listed = client.get("/api/experts")

    assert listed.status_code == 200
    rows = listed.json()
    assert [row["id"] for row in rows] == ["clinical-team", "research", "writer"]
    assert rows[0]["expertType"] == "team"
    assert "name" not in rows[0]
    assert "type" not in rows[0]
    assert rows[0]["displayName"]["zh"] == "clinical-team 展示名"
    assert rows[0]["profession"]["en"] == "clinical-team"
    assert rows[0]["description"]
    assert rows[0]["avatarUrl"] == "/api/experts/clinical-team/avatar"
    assert rows[0]["members"][0]["role"] == "lead"

    detail = client.get("/api/experts/writer")

    assert detail.status_code == 200
    assert detail.json()["displayName"]["zh"] == "writer 展示名"
    assert detail.json()["skills"] == [
        {"id": "writer:draft", "name": "draft", "description": "draft description"}
    ]
    assert detail.json()["prompt"] == "writer persona prompt"


def test_create_session_selects_expert_and_session_detail_returns_state(
    api_client: tuple[TestClient, FakeRedisSessionStore],
) -> None:
    client, _ = api_client

    created = client.post("/api/sessions", json={"expert_id": "writer"})

    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert created.json()["expert_state"] == {
        "expert_id": "writer",
        "source": "user",
        "selected_skill_ids": [],
        "unavailable_reason": None,
    }
    detail = client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["expert_state"] == created.json()["expert_state"]


def test_switch_and_cancel_expert_return_authoritative_state(
    api_client: tuple[TestClient, FakeRedisSessionStore],
) -> None:
    client, store = api_client

    selected = client.post(
        f"/api/sessions/{SESSION_ID}/expert", json={"expert_id": "clinical-team"}
    )

    assert selected.status_code == 200
    assert selected.json() == {
        "expert_id": "clinical-team",
        "source": "user",
        "selected_skill_ids": ["research:search"],
        "unavailable_reason": None,
    }

    cancelled = client.delete(f"/api/sessions/{SESSION_ID}/expert")

    assert cancelled.status_code == 200
    assert cancelled.json() == {
        "expert_id": None,
        "source": "user",
        "selected_skill_ids": ["research:search"],
        "unavailable_reason": None,
    }
    persisted = asyncio.run(store.load(SESSION_ID))
    assert persisted is not None
    assert persisted.header.last_user_prompt_expert_selection == "clinical-team"


def test_expert_avatar_served_from_manifest_path(
    api_client: tuple[TestClient, FakeRedisSessionStore], tmp_path: Path
) -> None:
    """头像唯一入口：按 manifest.avatar 读取包内文件；文件缺失时 404（不猜文件名）。"""
    client, _ = api_client

    assert client.get("/api/experts/writer/avatar").status_code == 404

    avatar = tmp_path / "experts" / "writer" / "avatars" / "lead.png"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"\x89PNG\r\n\x1a\n")

    served = client.get("/api/experts/writer/avatar")
    assert served.status_code == 200
    assert served.content == b"\x89PNG\r\n\x1a\n"


def test_skill_list_is_grouped_by_package_and_filters_disabled(
    api_client: tuple[TestClient, FakeRedisSessionStore],
) -> None:
    client, _ = api_client

    response = client.get("/api/skills")

    assert response.status_code == 200
    groups = response.json()
    assert [group["expert_name"] for group in groups] == ["research", "writer"]
    research = groups[0]
    assert research["expert_description"] == "research package description"
    skill_ids = [skill["id"] for skill in research["skills"]]
    assert "research:search" in skill_ids
    assert "research:disabled" not in skill_ids
    search = next(skill for skill in research["skills"] if skill["id"] == "research:search")
    assert search["name"] == "search"
    assert search["name_zh"] == "search 中文名"
    assert groups[1]["skills"][0]["id"] == "writer:draft"


def test_replace_and_remove_skill_selection(
    api_client: tuple[TestClient, FakeRedisSessionStore],
) -> None:
    client, store = api_client

    replaced = client.put(
        f"/api/sessions/{SESSION_ID}/skills",
        json={
            "selected_skill_ids": [
                "writer:draft",
                "research:search",
                "writer:draft",
            ]
        },
    )

    assert replaced.status_code == 200
    assert replaced.json() == {
        "session_id": SESSION_ID,
        "selected_skill_ids": ["writer:draft", "research:search"],
    }

    removed = client.delete(f"/api/sessions/{SESSION_ID}/skills/research:search")

    assert removed.status_code == 200
    assert removed.json()["selected_skill_ids"] == ["writer:draft"]
    persisted = asyncio.run(store.load(SESSION_ID))
    assert persisted is not None
    assert persisted.header.selected_skill_ids == ["writer:draft"]


@pytest.mark.parametrize(
    ("expert_id", "status", "code"),
    [
        ("missing", 404, "EXPERT_NOT_FOUND"),
        ("broken", 409, "EXPERT_UNAVAILABLE"),
        ("disabled-expert", 409, "MODULE_DISABLED"),
    ],
)
def test_expert_selection_and_detail_reject_unavailable_ids(
    api_client: tuple[TestClient, FakeRedisSessionStore],
    expert_id: str,
    status: int,
    code: str,
) -> None:
    client, _ = api_client

    selected = client.post(
        f"/api/sessions/{SESSION_ID}/expert", json={"expert_id": expert_id}
    )
    detail = client.get(f"/api/experts/{expert_id}")

    assert selected.status_code == status
    assert _error_code(selected) == code
    assert detail.status_code == status
    assert _error_code(detail) == code


def test_skill_selection_rejects_disabled_unknown_and_over_limit(
    api_client: tuple[TestClient, FakeRedisSessionStore],
) -> None:
    client, store = api_client

    disabled = client.put(
        f"/api/sessions/{SESSION_ID}/skills",
        json={"selected_skill_ids": ["research:disabled"]},
    )
    disabled_expert = client.put(
        f"/api/sessions/{SESSION_ID}/skills",
        json={"selected_skill_ids": ["disabled-expert:blocked"]},
    )
    unknown = client.put(
        f"/api/sessions/{SESSION_ID}/skills",
        json={"selected_skill_ids": ["research:missing"]},
    )
    over_limit = client.put(
        f"/api/sessions/{SESSION_ID}/skills",
        json={"selected_skill_ids": [f"research:skill-{i}" for i in range(21)]},
    )

    assert disabled.status_code == 409
    assert _error_code(disabled) == "MODULE_DISABLED"
    assert disabled_expert.status_code == 409
    assert _error_code(disabled_expert) == "MODULE_DISABLED"
    assert unknown.status_code == 404
    assert _error_code(unknown) == "EXPERT_NOT_FOUND"
    assert over_limit.status_code == 400
    assert _error_code(over_limit) == "SKILL_SELECTION_LIMIT"
    persisted = asyncio.run(store.load(SESSION_ID))
    assert persisted is not None
    assert persisted.header.selected_skill_ids == ["research:search"]
