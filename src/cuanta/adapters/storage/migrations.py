from __future__ import annotations

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL DEFAULT '',
        ended_at TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        trace_id TEXT NOT NULL DEFAULT '',
        hu_ref TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '',
        prompt_hash TEXT NOT NULL DEFAULT '',
        cost_usd REAL NOT NULL DEFAULT 0,
        parent_id TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_runs_trace ON runs(trace_id);
    CREATE INDEX idx_runs_kind ON runs(kind);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        prompt_id TEXT NOT NULL DEFAULT '',
        trace_id TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        tool_name TEXT NOT NULL DEFAULT '',
        tool_use_id TEXT NOT NULL DEFAULT '',
        tool_result_bytes INTEGER NOT NULL DEFAULT 0,
        tool_input_bytes INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        success INTEGER,
        query_source TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        command TEXT NOT NULL DEFAULT '',
        ts TEXT NOT NULL DEFAULT '',
        raw TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_events_run ON events(run_id);
    CREATE INDEX idx_events_trace ON events(trace_id);
    CREATE INDEX idx_events_session ON events(session_id);
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE TABLE test_runs (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL DEFAULT '',
        runner TEXT NOT NULL,
        command TEXT NOT NULL,
        status TEXT NOT NULL,
        passed INTEGER NOT NULL,
        failed INTEGER NOT NULL,
        errored INTEGER NOT NULL,
        skipped INTEGER NOT NULL,
        duration_s REAL NOT NULL,
        capsule_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        exit_code INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_test_runs_run ON test_runs(run_id);
    CREATE TABLE signatures (
        test_run_id TEXT NOT NULL,
        signature_id TEXT NOT NULL,
        error TEXT NOT NULL,
        verbatim TEXT NOT NULL,
        first_test TEXT NOT NULL,
        location TEXT NOT NULL,
        tests INTEGER NOT NULL,
        PRIMARY KEY (test_run_id, signature_id)
    );
    CREATE INDEX idx_signatures_id ON signatures(signature_id);
    CREATE TABLE capsules (
        id TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL,
        path TEXT NOT NULL,
        kind TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        lines INTEGER NOT NULL,
        summary TEXT NOT NULL,
        created_at TEXT NOT NULL,
        run_id TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL DEFAULT '',
        backend TEXT NOT NULL,
        primitive TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer TEXT NOT NULL,
        confidence REAL NOT NULL,
        latency_ms INTEGER NOT NULL,
        cost_usd REAL NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE file_snapshots (
        run_id TEXT NOT NULL,
        phase TEXT NOT NULL,
        path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        PRIMARY KEY (run_id, phase, path)
    );
    CREATE TABLE baselines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        bytes INTEGER NOT NULL,
        tokens INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT ''
    );
    """,
    """
    CREATE TABLE runs_nullable_cost (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL DEFAULT '',
        ended_at TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        trace_id TEXT NOT NULL DEFAULT '',
        hu_ref TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '',
        prompt_hash TEXT NOT NULL DEFAULT '',
        cost_usd REAL DEFAULT 0,
        parent_id TEXT NOT NULL DEFAULT ''
    );
    INSERT INTO runs_nullable_cost SELECT
        id, kind, engine, model, started_at, ended_at, status, trace_id, hu_ref, scope,
        prompt_hash,
        CASE WHEN engine = 'codex' AND cost_usd = 0 AND status != 'running' THEN NULL
             ELSE cost_usd END,
        parent_id
    FROM runs;
    DROP TABLE runs;
    ALTER TABLE runs_nullable_cost RENAME TO runs;
    CREATE INDEX idx_runs_trace ON runs(trace_id);
    CREATE INDEX idx_runs_kind ON runs(kind)
    """,
    """
    CREATE TABLE routing_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL DEFAULT '',
        task_type TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        tier TEXT NOT NULL,
        engine TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        confidence REAL,
        backend TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT '',
        cost_usd REAL,
        retries INTEGER NOT NULL DEFAULT 0,
        accepted INTEGER,
        created_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_routing_run ON routing_decisions(run_id);
    CREATE INDEX idx_routing_role ON routing_decisions(task_type, role, tier)
    """,
    """
    CREATE TABLE route_audits (
        run_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        planned TEXT NOT NULL DEFAULT '',
        actual TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        cause TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (run_id, agent)
    )
    """,
    """
    ALTER TABLE decisions ADD COLUMN preview INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE decisions ADD COLUMN request_hash TEXT NOT NULL DEFAULT '';
    CREATE INDEX idx_decisions_run ON decisions(run_id);
    CREATE INDEX idx_decisions_hash ON decisions(request_hash, question)
    """,
    """
    ALTER TABLE runs ADD COLUMN task_type TEXT NOT NULL DEFAULT '';
    ALTER TABLE runs ADD COLUMN depth TEXT NOT NULL DEFAULT ''
    """,
    """
    ALTER TABLE events ADD COLUMN effort TEXT NOT NULL DEFAULT '';
    ALTER TABLE events ADD COLUMN ttft_ms INTEGER NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE decisions ADD COLUMN fallback_error TEXT NOT NULL DEFAULT '';
    ALTER TABLE decisions ADD COLUMN fallback_from TEXT NOT NULL DEFAULT ''
    """,
    """
    ALTER TABLE runs ADD COLUMN max_turns INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE runs ADD COLUMN turns INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE runs ADD COLUMN end_reason TEXT NOT NULL DEFAULT ''
    """,
    """
    ALTER TABLE runs ADD COLUMN cost_source TEXT NOT NULL DEFAULT 'unknown';
    CREATE TABLE events_nullable_cost (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        prompt_id TEXT NOT NULL DEFAULT '',
        trace_id TEXT NOT NULL DEFAULT '',
        agent TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL,
        tool_name TEXT NOT NULL DEFAULT '',
        tool_use_id TEXT NOT NULL DEFAULT '',
        tool_result_bytes INTEGER NOT NULL DEFAULT 0,
        tool_input_bytes INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        success INTEGER,
        query_source TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        command TEXT NOT NULL DEFAULT '',
        ts TEXT NOT NULL DEFAULT '',
        raw TEXT NOT NULL DEFAULT '',
        effort TEXT NOT NULL DEFAULT '',
        ttft_ms INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO events_nullable_cost (id, run_id, source, session_id, prompt_id, trace_id,
    agent, kind, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    reasoning_tokens, cost_usd, tool_name, tool_use_id, tool_result_bytes, tool_input_bytes,
    duration_ms, success, query_source, file_path, command, ts, raw, effort, ttft_ms) SELECT
    id, run_id, source, session_id, prompt_id, trace_id, agent, kind, model, input_tokens,
    output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, cost_usd,
    tool_name, tool_use_id, tool_result_bytes, tool_input_bytes, duration_ms, success,
    query_source, file_path, command, ts, raw, effort, ttft_ms FROM events;
    INSERT INTO sqlite_sequence(name, seq)
    SELECT 'events_nullable_cost', seq FROM sqlite_sequence WHERE name = 'events'
    AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'events_nullable_cost');
    UPDATE sqlite_sequence SET seq = MAX(seq, (
        SELECT COALESCE(MAX(seq), 0) FROM sqlite_sequence WHERE name = 'events'
    )) WHERE name = 'events_nullable_cost';
    DROP TABLE events;
    ALTER TABLE events_nullable_cost RENAME TO events;
    CREATE INDEX idx_events_run ON events(run_id);
    CREATE INDEX idx_events_trace ON events(trace_id);
    CREATE INDEX idx_events_session ON events(session_id);
    CREATE INDEX idx_events_ts ON events(ts);
    CREATE TABLE decisions_nullable_cost (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL DEFAULT '',
        backend TEXT NOT NULL,
        primitive TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer TEXT NOT NULL,
        confidence REAL NOT NULL,
        latency_ms INTEGER NOT NULL,
        cost_usd REAL,
        outcome TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        preview INTEGER NOT NULL DEFAULT 0,
        request_hash TEXT NOT NULL DEFAULT '',
        fallback_error TEXT NOT NULL DEFAULT '',
        fallback_from TEXT NOT NULL DEFAULT ''
    );
    INSERT INTO decisions_nullable_cost (id, run_id, backend, primitive, question, options,
    answer, confidence, latency_ms, cost_usd, outcome, created_at, preview, request_hash,
    fallback_error, fallback_from) SELECT id, run_id, backend, primitive, question, options,
    answer, confidence, latency_ms, cost_usd, outcome, created_at, preview, request_hash,
    fallback_error, fallback_from FROM decisions;
    INSERT INTO sqlite_sequence(name, seq)
    SELECT 'decisions_nullable_cost', seq FROM sqlite_sequence WHERE name = 'decisions'
    AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'decisions_nullable_cost');
    UPDATE sqlite_sequence SET seq = MAX(seq, (
        SELECT COALESCE(MAX(seq), 0) FROM sqlite_sequence WHERE name = 'decisions'
    )) WHERE name = 'decisions_nullable_cost';
    DROP TABLE decisions;
    ALTER TABLE decisions_nullable_cost RENAME TO decisions;
    CREATE INDEX idx_decisions_run ON decisions(run_id);
    CREATE INDEX idx_decisions_hash ON decisions(request_hash, question)
    """,
)

LATEST_VERSION = len(MIGRATIONS)
