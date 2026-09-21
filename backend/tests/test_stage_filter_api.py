"""GET /api/jobs/{id}/stages 过滤接口测试（sqlite + 依赖覆盖，无需 Postgres）。

覆盖迭代约定：
1. 状态多选 + 消息关键字均通过查询参数下发；
2. 返回的 stage_order 集合 == 未过滤全量中命中的阶段序号（不重编号、不藏行）；
3. 两个角色（bioops / auditor）都可查询。
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Job, JobStage


_DB_PATH = Path(tempfile.mkdtemp(prefix="fq-stage-filter-")) / "test.db"
test_engine = create_engine(
    f"sqlite:///{_DB_PATH}", connect_args={"check_same_thread": False}
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

BROKEN_STAGES = [
    ("ParseActor", "failed", "第 3 行分隔符必须以 + 开头，实际为: 'THIS_IS_NOT_A_PLUS_LINE'"),
    ("QualityHistActor", "skipped", "因 ParseActor 失败而跳过"),
    ("NContentActor", "skipped", "因 ParseActor 失败而跳过"),
    ("ReportActor", "skipped", "因 ParseActor 失败而跳过"),
]

SUCCESS_STAGES = [
    ("ParseActor", "success", "完成"),
    ("QualityHistActor", "success", "完成"),
    ("NContentActor", "success", "完成"),
    ("ReportActor", "success", "完成"),
]

BROKEN_JOB_ID = 0
SUCCESS_JOB_ID = 0


def _seed_job(db, sample_name: str, status: str, stages: list[tuple[str, str, str]]) -> int:
    job = Job(
        sample_id=None,
        sample_name=sample_name,
        status=status,
        created_by="bioops",
        fastq_snapshot="@X\nACGT\n+\nIIII\n",
    )
    db.add(job)
    db.flush()
    for order, (actor, st_status, message) in enumerate(stages):
        db.add(
            JobStage(
                job_id=job.id,
                actor_name=actor,
                stage_order=order,
                status=st_status,
                message=message,
            )
        )
    db.commit()
    return job.id


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    global BROKEN_JOB_ID, SUCCESS_JOB_ID
    Base.metadata.create_all(bind=test_engine)
    db = TestSession()
    try:
        BROKEN_JOB_ID = _seed_job(db, "demo-broken-malformed", "failed", BROKEN_STAGES)
        SUCCESS_JOB_ID = _seed_job(db, "demo-good-r1", "success", SUCCESS_STAGES)
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="module")
def client():
    # 不使用上下文管理器：避免触发 lifespan 去连默认 Postgres
    return TestClient(app)


def _token(client, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def bioops_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'bioops', 'fastq123456')}"}


@pytest.fixture(scope="module")
def auditor_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'auditor', 'audit123456')}"}


def _orders(items):
    return [s["stage_order"] for s in items]


def test_no_filter_returns_full_set_in_order(client, bioops_headers):
    resp = client.get(f"/api/jobs/{BROKEN_JOB_ID}/stages", headers=bioops_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert _orders(body) == [0, 1, 2, 3]
    assert [s["actor_name"] for s in body] == [s[0] for s in BROKEN_STAGES]


def test_status_multi_select(client, bioops_headers):
    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params=[("statuses", "failed"), ("statuses", "skipped")],
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    assert _orders(resp.json()) == [0, 1, 2, 3]

    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params=[("statuses", "skipped")],
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    # 序号保持全量编号，不重排为 0,1,2
    assert _orders(resp.json()) == [1, 2, 3]


def test_keyword_matches_message_case_insensitive(client, bioops_headers):
    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params={"q": "必须以"},
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _orders(body) == [0]
    assert body[0]["actor_name"] == "ParseActor"

    # 关键字只匹配 message：skipped 阶段消息含 "ParseActor"，失败阶段消息不含
    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params={"q": "parseactor"},  # 小写验证大小写不敏感
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    assert _orders(resp.json()) == [1, 2, 3]


def test_selftest_scenario_broken_job_failed_plus_parse_keyword(client, bioops_headers):
    """自测场景：损坏作业只勾 failed + 解析相关关键字 → 仅解析失败项，序号与全量一致。"""
    keyword = "必须以"
    full = client.get(f"/api/jobs/{BROKEN_JOB_ID}/stages", headers=bioops_headers).json()
    expected_orders = [
        s["stage_order"]
        for s in full
        if s["status"] == "failed" and keyword in (s["message"] or "")
    ]

    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params=[("statuses", "failed"), ("q", keyword)],
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [s["actor_name"] for s in body] == ["ParseActor"]
    assert _orders(body) == expected_orders == [0]


def test_filtered_set_equals_matching_subset_of_full_set(client, bioops_headers):
    """任意过滤结果都必须等于未过滤全量中满足条件的子集（禁止前端藏行式假过滤）。"""
    cases = [
        [("statuses", "success")],
        [("statuses", "failed"), ("statuses", "success")],
        [("q", "完成")],
        [("statuses", "success"), ("q", "完成")],
        [("statuses", "pending")],
        [("q", "不存在的关键字")],
    ]
    for job_id in (BROKEN_JOB_ID, SUCCESS_JOB_ID):
        full = client.get(f"/api/jobs/{job_id}/stages", headers=bioops_headers).json()
        for params in cases:
            resp = client.get(
                f"/api/jobs/{job_id}/stages", params=params, headers=bioops_headers
            )
            assert resp.status_code == 200, params
            wanted_statuses = {v for k, v in params if k == "statuses"}
            wanted_q = next((v for k, v in params if k == "q"), None)
            expected = [
                s["stage_order"]
                for s in full
                if (not wanted_statuses or s["status"] in wanted_statuses)
                and (wanted_q is None or wanted_q.lower() in (s["message"] or "").lower())
            ]
            assert _orders(resp.json()) == expected, params


def test_invalid_status_rejected(client, bioops_headers):
    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params=[("statuses", "bogus")],
        headers=bioops_headers,
    )
    assert resp.status_code == 400
    assert "非法阶段状态" in resp.json()["detail"]


def test_auditor_can_query_stages(client, auditor_headers):
    resp = client.get(
        f"/api/jobs/{BROKEN_JOB_ID}/stages",
        params=[("statuses", "failed")],
        headers=auditor_headers,
    )
    assert resp.status_code == 200
    assert _orders(resp.json()) == [0]


def test_unauthenticated_rejected(client):
    resp = client.get(f"/api/jobs/{BROKEN_JOB_ID}/stages")
    assert resp.status_code == 401


def test_unknown_job_404(client, bioops_headers):
    resp = client.get("/api/jobs/999999/stages", headers=bioops_headers)
    assert resp.status_code == 404
