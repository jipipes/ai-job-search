from scripts.thefounders_jobs import BASE, discover, parse_posting


def test_discovery_deduplicates_and_filters_urls():
    url = BASE + "/job_posting/example"
    xml = f"""
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>{url}</loc></url>
      <url><loc>{url}?utm_source=example</loc></url>
      <url><loc>{BASE}/recruit-ugc3x</loc></url>
      <url><loc>https://example.org/job_posting/other</loc></url>
    </urlset>
    """
    assert discover(xml) == [url]


def test_normalizes_sections_and_numbered_questions():
    html = """
    <div class="PostingLayout1Header__Header-demo">
      <h1>BI &amp; Data Engineer</h1>
      <span class="LabelGroup__Label-demo">신입</span>
      <span class="LabelGroup__Label-demo">인턴</span>
      <span class="LabelGroup__Label-demo">채용 시 마감</span>
    </div>
    <div class="PostingHTMLContent__Content-demo">
      <h2>ㅣ 자격 요건</h2>
      <ul><li>SQL</li><li>Python</li><li>대시보드 또는 리포트 제작 경험</li></ul>
      <h2>ㅣ 우대 사항</h2>
      <ul><li>Airflow, dbt</li></ul>
      <h2>ㅣ 지원 방법</h2>
      <ul><li>자기소개서<br>1. 지원 동기<br>2. 몰두한 경험<br>3. 프로젝트 경험</li></ul>
    </div>
    """
    result = parse_posting(html, BASE + "/job_posting/example")
    assert result["requirements"] == ["SQL", "Python", "대시보드 또는 리포트 제작 경험"]
    assert result["preferred_requirements"] == ["Airflow, dbt"]
    assert result["deadline_type"] == "until_filled"
    assert result["deadline_date"] is None
    assert result["entry_level_eligible"] is True
    assert result["application_questions"] == ["지원 동기", "몰두한 경험", "프로젝트 경험"]
