#!/usr/bin/env python
"""R03 - Data to Arabic RTL DOCX/PPTX report (flagship).

Attendance summary per employee (Arabic names/departments from the seed) -> Code builds a docgen DocRequest
(lang "ar", RTL, Western numerals) -> docgen /render/docx and /render/pptx -> data/out/, MinIO reports/,
documents rows -> HR e-mail with the DOCX attached.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from n8n_builder import (Workflow, catalog_id, code, email, http, manual_trigger, postgres_insert,  # noqa: E402
                         postgres_query, s3_upload, set_fields, write_file)

ATTENDANCE_SQL = """select e.employee_no, e.full_name, e.full_name_ar, e.department, e.department_ar, e.title,
       count(a.id) filter (where a.status = 'present')::int as present,
       count(a.id) filter (where a.status = 'late')::int    as late,
       count(a.id) filter (where a.status = 'absent')::int  as absent,
       count(a.id) filter (where a.status = 'leave')::int   as on_leave,
       count(a.id)::int as days,
       min(a.work_date)::text as period_start, max(a.work_date)::text as period_end
from employees e
left join attendance a on a.employee_id = e.id
group by e.id
order by e.department_ar, e.full_name_ar"""

BUILD_JS = r"""
// Rows per employee -> one docgen DocRequest. Arabic labels, Western (ASCII) numerals, RTL handled by docgen.
const rows = $input.all().map(i => i.json);
const cfg = $('Config').first().json;
const start = rows.map(r => r.period_start).filter(Boolean).sort()[0] || '';
const end = rows.map(r => r.period_end).filter(Boolean).sort().slice(-1)[0] || '';
const pct = (a, b) => b ? Math.round((a / b) * 100) : 0;
const sum = (k) => rows.reduce((a, r) => a + Number(r[k] || 0), 0);
const totals = { present: sum('present'), late: sum('late'), absent: sum('absent'), on_leave: sum('on_leave'), days: sum('days') };

// per department
const byDept = {};
for (const r of rows) {
  const d = byDept[r.department_ar] ||= { name: r.department_ar, en: r.department, employees: 0, present: 0, late: 0, absent: 0, on_leave: 0, days: 0 };
  d.employees += 1; for (const k of ['present', 'late', 'absent', 'on_leave', 'days']) d[k] += Number(r[k] || 0);
}
const depts = Object.values(byDept).sort((a, b) => pct(b.present, b.days) - pct(a.present, a.days));
const worst = [...rows].sort((a, b) => (b.absent + b.late) - (a.absent + a.late)).slice(0, 5);

const doc = {
  title: 'تقرير الحضور والانصراف',
  subtitle: `الفترة من ${start} إلى ${end} - ${rows.length} موظفاً`,
  lang: 'ar', rtl: true,
  footer: 'أُنشئ هذا التقرير تلقائياً بواسطة Automation Lab (R03) - بيانات تجريبية',
  sections: [
    { heading: 'الملخص التنفيذي',
      paragraphs: [
        `بلغت نسبة الحضور الإجمالية ${pct(totals.present, totals.days)}% خلال الفترة، مع ${totals.late} حالة تأخير و${totals.absent} حالة غياب و${totals.on_leave} يوم إجازة من أصل ${totals.days} يوم عمل مسجّل.`,
        `أعلى الأقسام حضوراً: ${depts[0]?.name || '-'} (${pct(depts[0]?.present, depts[0]?.days)}%)، وأقلها: ${depts[depts.length - 1]?.name || '-'} (${pct(depts[depts.length - 1]?.present, depts[depts.length - 1]?.days)}%).`,
      ] },
    { heading: 'الحضور حسب القسم',
      table: { columns: ['القسم', 'الموظفون', 'حضور', 'تأخير', 'غياب', 'إجازة', 'نسبة الحضور'],
               rows: depts.map(d => [d.name, d.employees, d.present, d.late, d.absent, d.on_leave, `${pct(d.present, d.days)}%`]) } },
    { heading: 'تفاصيل الموظفين',
      table: { columns: ['الرقم الوظيفي', 'الاسم', 'القسم', 'حضور', 'تأخير', 'غياب', 'إجازة'],
               rows: rows.map(r => [r.employee_no, r.full_name_ar, r.department_ar, r.present, r.late, r.absent, r.on_leave]) } },
    { heading: 'ملاحظات وتوصيات',
      bullets: [
        ...worst.map(r => `${r.full_name_ar} (${r.department_ar}): ${r.absent} غياب و${r.late} تأخير - يُنصح بالمتابعة`),
        'الأرقام في هذا التقرير أرقام غربية (0-9) عمداً لتسهيل الفرز والمقارنة في الجداول.',
      ] },
  ],
};
const stamp = end.replace(/-/g, '') || $now.toFormat('yyyyLLdd');
return [{ json: { doc, base_name: `attendance-report-ar-${stamp}`, employees: rows.length, totals,
                  period: { start, end }, report_to: cfg.report_to,
                  subject: `تقرير الحضور والانصراف ${start} - ${end}` } }];
"""


def build() -> Workflow:
    wf = Workflow("R03", "arabic-rtl-report", "Data to Arabic RTL DOCX/PPTX Report", tags=["Documents"],
                  error_workflow=catalog_id("P01"),
                  description="Bilingual-ready Arabic RTL attendance report as DOCX and PPTX via docgen.")
    trg = manual_trigger(wf, "Run once (manual / CLI)")
    cfg = set_fields(wf, "Config", {"report_to": "hr@lab.local"})
    q = postgres_query(wf, "Attendance per employee", ATTENDANCE_SQL)
    build_ = code(wf, "Build Arabic report (DocRequest)", BUILD_JS)
    docx = http(wf, "Render DOCX (docgen)", "http://docgen:8090/render/docx", method="POST",
                json_body="={{ JSON.stringify({ ...$json.doc, filename: $json.base_name + '.docx' }) }}",
                response="file", timeout_ms=120000).retry(3, 2000)
    pptx = http(wf, "Render PPTX (docgen)", "http://docgen:8090/render/pptx", method="POST",
                json_body="={{ JSON.stringify({ ...$json.doc, filename: $json.base_name + '.pptx' }) }}",
                response="file", timeout_ms=120000).retry(3, 2000)
    save_docx = write_file(wf, "Write DOCX", "=/home/node/.n8n-files/data/out/{{ $('Build Arabic report (DocRequest)').item.json.base_name }}.docx")
    save_pptx = write_file(wf, "Write PPTX", "=/home/node/.n8n-files/data/out/{{ $('Build Arabic report (DocRequest)').item.json.base_name }}.pptx")
    up_docx = s3_upload(wf, "Upload DOCX (MinIO)", "reports", "=attendance/{{ $('Build Arabic report (DocRequest)').item.json.base_name }}.docx").retry(3, 1000)
    up_pptx = s3_upload(wf, "Upload PPTX (MinIO)", "reports", "=attendance/{{ $('Build Arabic report (DocRequest)').item.json.base_name }}.pptx").retry(3, 1000)
    doc_docx = postgres_insert(wf, "Record DOCX", "documents", {
        "kind": "report-docx-ar",
        "file_name": "={{ $('Build Arabic report (DocRequest)').item.json.base_name }}.docx",
        "storage_key": "=s3://reports/attendance/{{ $('Build Arabic report (DocRequest)').item.json.base_name }}.docx",
        "meta": "={{ JSON.stringify({ lang: 'ar', rtl: true, employees: $('Build Arabic report (DocRequest)').item.json.employees, period: $('Build Arabic report (DocRequest)').item.json.period, totals: $('Build Arabic report (DocRequest)').item.json.totals }) }}",
    })
    doc_pptx = postgres_insert(wf, "Record PPTX", "documents", {
        "kind": "report-pptx-ar",
        "file_name": "={{ $('Build Arabic report (DocRequest)').item.json.base_name }}.pptx",
        "storage_key": "=s3://reports/attendance/{{ $('Build Arabic report (DocRequest)').item.json.base_name }}.pptx",
        "meta": "={{ JSON.stringify({ lang: 'ar', rtl: true, employees: $('Build Arabic report (DocRequest)').item.json.employees, period: $('Build Arabic report (DocRequest)').item.json.period }) }}",
    })
    mail = email(wf, "Email HR (Mailpit)", "={{ $('Build Arabic report (DocRequest)').item.json.report_to }}",
                 "={{ $('Build Arabic report (DocRequest)').item.json.subject }}",
                 html="=<div dir=\"rtl\" style=\"font-family:sans-serif\"><p>مرفق تقرير الحضور والانصراف للفترة "
                      "{{ $('Build Arabic report (DocRequest)').item.json.period.start }} - "
                      "{{ $('Build Arabic report (DocRequest)').item.json.period.end }} "
                      "({{ $('Build Arabic report (DocRequest)').item.json.employees }} موظفاً).</p>"
                      "<p>نسخة العرض التقديمي (PPTX) محفوظة في MinIO تحت reports/attendance/.</p></div>",
                 attachments="data").retry(3, 2000)
    wf.chain(trg, cfg, q, build_)
    wf.chain(build_, docx, save_docx)
    wf.chain(build_, pptx, save_pptx)
    wf.chain(save_docx, up_docx, doc_docx)
    wf.connect(save_docx, mail)
    wf.chain(save_pptx, up_pptx, doc_pptx)
    wf.sticky(
        "## R03 - Arabic RTL report (flagship)\n"
        "One query -> a Code node assembles a structured `DocRequest` (Arabic title, sections, tables, bullets) "
        "-> **docgen** renders DOCX (python-docx, `w:bidi`/`w:rtl`, Amiri font) and PPTX (python-pptx, `rtl=\"1\"`).\n\n"
        "Shaping and bidi are handled by the document renderers (Word/PowerPoint/LibreOffice), so the text stays "
        "real, searchable Arabic - not pre-shaped glyphs. Numerals are Western on purpose.\n\n"
        "Outputs: `data/out/attendance-report-ar-<date>.docx|pptx`, MinIO `reports/attendance/`, `documents` rows, HR e-mail.",
        pos=(-40, -360), width=680, height=260)
    return wf


if __name__ == "__main__":
    build().save()
