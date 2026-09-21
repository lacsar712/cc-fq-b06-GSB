"""Pytest fixtures: in-memory SQLite + FastAPI TestClient with overridden DB."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Job, Sample
from app.pipeline.runner import create_job_stages, run_pipeline_sync


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # 不使用 with 触发 lifespan（lifespan 会连真实 Postgres 建表）；测试库已在 db_session 建好
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _make_job(db, name, fastq_text, broken=False):
    sample = Sample(
        name=name,
        description="test",
        is_broken=broken,
        fastq_content=fastq_text,
    )
    db.add(sample)
    db.commit()
    job = Job(
        sample_id=sample.id,
        sample_name=name,
        status="pending",
        created_by="bioops",
        fastq_snapshot=fastq_text,
    )
    db.add(job)
    db.commit()
    create_job_stages(db, job.id)
    run_pipeline_sync(db, job)
    return job


@pytest.fixture()
def broken_job(db_session):
    fastq = "@BROKEN_001\nACGTACGT\nTHIS_IS_NOT_A_PLUS_LINE\nIIIIIIII\n"
    return _make_job(db_session, "broken-sample", fastq, broken=True)


@pytest.fixture()
def good_job(db_session):
    fastq = "@SEQ1\nACGTACGT\n+\nIIIIHHHH\n@SEQ2\nNNNNACGT\n+\nIIIIIIII\n"
    return _make_job(db_session, "good-sample", fastq, broken=False)


@pytest.fixture()
def bioops_headers(client):
    resp = client.post(
        "/api/auth/login", json={"username": "bioops", "password": "fastq123456"}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def auditor_headers(client):
    resp = client.post(
        "/api/auth/login", json={"username": "auditor", "password": "audit123456"}
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
