PRAGMA foreign_keys = ON;

CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    work_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    salary_min REAL,
    salary_max REAL,
    salary_period TEXT NOT NULL DEFAULT 'Annual',
    currency TEXT NOT NULL DEFAULT 'USD',
    applied_date TEXT,
    next_action_date TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_next_action ON applications(next_action_date);
CREATE INDEX idx_applications_company ON applications(company);

CREATE TABLE application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_application_events_application
    ON application_events(application_id, occurred_at DESC, id DESC);

INSERT INTO applications (
    id, company, role, location, work_mode, status, source, url,
    salary_min, salary_max, salary_period, currency, applied_date,
    next_action_date, notes, created_at, updated_at
) VALUES
    (1, 'Acme Labs', 'Python Developer', 'Worldwide', 'Remote', 'Applied',
     'CSV import', 'https://jobs.example.com/acme-python', 35, 50, 'Hourly', 'USD',
     '2026-08-20', '2026-08-28', 'Imported from the example CSV.',
     '2026-08-20T09:00:00+00:00', '2026-08-20T09:00:00+00:00'),
    (2, '星河科技', 'AI 评测工程师', '日本', 'Remote', 'Wishlist',
     'Company site', 'https://jobs.example.com/xinghe', NULL, NULL, 'Annual', 'CNY',
     NULL, '2026-09-01', '中文备注：等待合适的合同工项目。',
     '2026-08-21T10:30:00+00:00', '2026-08-21T10:30:00+00:00'),
    (3, '東京 Labs', 'Frontend Engineer', 'Tokyo, Japan', 'Hybrid', 'Interview',
     'Referral', 'https://jobs.example.com/tokyo-frontend', 5000000, 6500000, 'Annual', 'JPY',
     '2026-08-10', '2026-08-27', 'Prepare a concise demo for the panel.',
     '2026-08-10T08:00:00+00:00', '2026-08-25T10:00:00+00:00'),
    (4, 'Orbit Systems', 'Backend Engineer', 'Singapore', 'Remote', 'Offer',
     'Remote board', 'https://jobs.example.com/orbit-backend', 30, 42, 'Hourly', 'USD',
     '2026-08-01', '2026-08-30', 'Review the contract and confirm availability.',
     '2026-08-01T11:00:00+00:00', '2026-08-26T12:00:00+00:00'),
    (5, 'Nocturne Data', 'QA Tester', 'Remote', 'Remote', 'Rejected',
     'LinkedIn', 'https://jobs.example.com/nocturne-qa', 20, 28, 'Hourly', 'USD',
     '2026-07-15', NULL, '',
     '2026-07-15T07:00:00+00:00', '2026-07-22T07:00:00+00:00');

INSERT INTO application_events (
    id, application_id, event_type, title, details, occurred_at, created_at
) VALUES
    (1, 1, 'applied', 'Application submitted', 'Submitted through the company application form.',
     '2026-08-20T09:00:00+00:00', '2026-08-20T09:00:00+00:00'),
    (2, 1, 'custom', 'Imported from example CSV', 'Original row retained for traceability.',
     '2026-08-20T09:00:00+00:00', '2026-08-20T09:01:00+00:00'),
    (3, 2, 'custom', 'Saved for later', 'Keep this role in the shortlist until the next review.',
     '2026-08-21T10:30:00+00:00', '2026-08-21T10:30:00+00:00'),
    (4, 3, 'applied', 'Application submitted', 'Referred by a former classmate.',
     '2026-08-10T08:00:00+00:00', '2026-08-10T08:00:00+00:00'),
    (5, 3, 'interview', 'Interview scheduled', 'Remote panel: 30 minutes.',
     '2026-08-25T10:00:00+00:00', '2026-08-24T09:00:00+00:00'),
    (6, 3, 'interview', 'Interview notes', '同じ時刻のイベントで順序を固定します。',
     '2026-08-25T10:00:00+00:00', '2026-08-25T11:00:00+00:00'),
    (7, 4, 'applied', 'Application submitted', 'Applied through a remote-work board.',
     '2026-08-01T11:00:00+00:00', '2026-08-01T11:00:00+00:00'),
    (8, 4, 'offer', 'Offer received', 'Review compensation, scope, and start date.',
     '2026-08-26T12:00:00+00:00', '2026-08-26T12:00:00+00:00'),
    (9, 5, 'applied', 'Application submitted', 'Submitted a short QA portfolio.',
     '2026-07-15T07:00:00+00:00', '2026-07-15T07:00:00+00:00'),
    (10, 5, 'rejection', 'Not selected', 'Keep the feedback for future applications.',
     '2026-07-22T07:00:00+00:00', '2026-07-22T07:00:00+00:00');

PRAGMA user_version = 3;
