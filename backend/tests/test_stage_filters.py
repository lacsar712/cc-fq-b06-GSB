"""API tests for server-side stage filtering on GET /jobs/{id}/stages."""

import pytest


def test_unfiltered_stages_full_ordering(client, broken_job, bioops_headers):
    resp = client.get(f"/api/jobs/{broken_job.id}/stages", headers=bioops_headers)
    assert resp.status_code == 200
    all_stages = resp.json()
    assert [s["stage_order"] for s in all_stages] == [0, 1, 2, 3]
    assert [s["status"] for s in all_stages] == ["failed", "skipped", "skipped", "skipped"]
    assert all_stages[0]["actor_name"] == "ParseActor"


def test_failed_status_with_parse_keyword_returns_only_parse_stage(
    client, broken_job, bioops_headers
):
    # 损坏作业：只勾失败 + 解析相关关键字（英文 actor 名）
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params=[("status", "failed"), ("keyword", "Parse")],
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["actor_name"] == "ParseActor"
    assert rows[0]["status"] == "failed"


def test_failed_status_with_chinese_parse_keyword_returns_only_parse_stage(
    client, broken_job, bioops_headers
):
    # 中文“解析”关键字应通过失败消息“解析失败：…”命中同一项
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"status": "failed", "keyword": "解析"},
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["actor_name"] == "ParseActor"


def test_filtered_stage_order_equals_unfiltered_index(client, broken_job, bioops_headers):
    # 关键约定：返回的阶段序号必须等于未过滤全量里命中的那些（此处 ParseActor 仍是第 1 项）
    full = client.get(
        f"/api/jobs/{broken_job.id}/stages", headers=bioops_headers
    ).json()
    filtered = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"status": "failed", "keyword": "解析"},
        headers=bioops_headers,
    ).json()

    hit_names = {s["actor_name"] for s in filtered}
    expected_orders = {s["stage_order"] for s in full if s["actor_name"] in hit_names}
    assert {s["stage_order"] for s in filtered} == expected_orders
    assert filtered[0]["stage_order"] == 0


def test_multi_status_multi_select(client, broken_job, bioops_headers):
    # 多选 failed + skipped：命中全部 4 个阶段；且序号保持全量编号
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params=[("status", "failed"), ("status", "skipped")],
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert [s["stage_order"] for s in rows] == [0, 1, 2, 3]


def test_comma_separated_multi_status(client, broken_job, bioops_headers):
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"status": "failed,skipped"},
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 4


def test_status_and_keyword_are_and_semantics(client, broken_job, bioops_headers):
    # ParseActor 虽匹配关键字，但状态不匹配 success -> 空结果（AND 语义）
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"status": "success", "keyword": "Parse"},
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_keyword_matches_skipped_messages(client, broken_job, bioops_headers):
    # 后续阶段消息为“因 ParseActor 失败而跳过”，关键字 Parse 也应命中它们
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"keyword": "Parse"},
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    assert {s["stage_order"] for s in resp.json()} == {0, 1, 2, 3}


def test_good_job_failed_filter_empty(client, good_job, bioops_headers):
    resp = client.get(
        f"/api/jobs/{good_job.id}/stages",
        params={"status": "failed"},
        headers=bioops_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_invalid_status_rejected(client, broken_job, bioops_headers):
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"status": "bogus"},
        headers=bioops_headers,
    )
    assert resp.status_code == 400


def test_auditor_can_filter_stages(client, broken_job, auditor_headers):
    # 两角色可看：审计员同样可以带查询参数请求阶段接口
    resp = client.get(
        f"/api/jobs/{broken_job.id}/stages",
        params={"status": "failed", "keyword": "解析"},
        headers=auditor_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["actor_name"] == "ParseActor"


def test_stages_require_auth(client, broken_job):
    resp = client.get(f"/api/jobs/{broken_job.id}/stages", params={"status": "failed"})
    assert resp.status_code == 401


def test_stages_of_missing_job_404(client, bioops_headers):
    resp = client.get("/api/jobs/9999/stages", headers=bioops_headers)
    assert resp.status_code == 404
