#!/usr/bin/env python3
"""Public career-source discovery and conservative internship tracking. Python 3.11+."""
import argparse, concurrent.futures, csv, datetime as dt, hashlib, html, io, json, re, sys, threading, time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urldefrag
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = 'https://raw.githubusercontent.com/aolofsson/awesome-semiconductor-startups/main/startups.csv'
CATEGORIES = ['ASIC','AI','CHIPLETS','EDA','FPGA','HPC','MEMORY','MEMS','MFG','NETWORKING','PHOTONICS','QUANTUM','RISC-V','SECURITY','SENSORS']
UA = 'SemiconductorInternshipTracker/1.0 (public job listings; low frequency)'
INTERN = re.compile(r'\b(intern(?:ship)?s?|co[ -]?op|working student|werkstudent\w*|student researcher|placement student|stage|stagiaire|praktikum)\b', re.I)
NONTECH = re.compile(r'\b(marketing|human resources|recruit(?:ing|ment)|finance|accounting|sales|legal|communications|graphic design|business development|people operations|growth ops|fundraising|investor relations|recruiting|hr)\b',re.I)
CAREER = re.compile(r'career|\bjobs?\b|join.{0,15}(team|us)|open positions|vacancies|opportunities|work with us', re.I)
ATS = re.compile(r'https?://(?:[\w.-]*greenhouse\.io|(?:jobs|jobs\.eu)\.lever\.co|jobs\.ashbyhq\.com|apply\.workable\.com|[\w-]+\.recruitee\.com)/[^\s<>"\']*', re.I)
FIT = {
 'Server / validation':r'\b(server|validation|bring.up|debug|platform|post.silicon|ras)\b',
 'RTL / verification':r'\b(rtl|asic|fpga|verilog|verification|digital design|soc)\b',
 'Physical design':r'\b(physical design|sta|timing|place.and.route|cadence|innovus)\b',
 'Interconnect / networking':r'\b(pcie|cxl|network|networking|interconnect|ethernet|photonics|optical)\b',
 'Embedded / boards':r'\b(embedded|firmware|pcb|board|microcontroller|stm32)\b',
 'AI / software':r'\b(software|python|compiler|machine learning|ai|ml|algorithm)\b'
}
LOCKS = {}; ROBOTS = {}; GLOBAL_LOCK = threading.Lock()

def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def read_json(path, default): return json.loads(path.read_text()) if path.exists() else default

def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    temp.replace(path)

class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True); self.links=[]; self.jsonld=[]; self.a=None; self.script=None; self.feed(text)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='a' and a.get('href'): self.a=[a['href'],'']
        if tag=='iframe' and a.get('src') and (ATS.search(a['src']) or CAREER.search(a['src'])): self.links.append((a['src'],'embedded jobs'))
        if tag=='script' and a.get('type')=='application/ld+json': self.script=''
    def handle_data(self, data):
        if self.a is not None: self.a[1]+=data
        if self.script is not None: self.script+=data
    def handle_endtag(self, tag):
        if tag=='a' and self.a is not None: self.links.append(tuple(self.a)); self.a=None
        if tag=='script' and self.script is not None:
            try: self.jsonld.append(json.loads(self.script))
            except ValueError: pass
            self.script=None

def raw_get(url, timeout=30):
    req=Request(url,headers={'User-Agent':UA,'Accept':'application/json,text/html;q=0.9,*/*;q=0.8'})
    with urlopen(req,timeout=timeout) as r:
        data=r.read(8_000_001)
        if len(data)>8_000_000: raise ValueError('Response exceeds 8 MB')
        return data.decode('utf-8',errors='replace'),r.url

def get(url):
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname: raise ValueError('Invalid URL')
    host=parsed.netloc
    with GLOBAL_LOCK: lock=LOCKS.setdefault(host,threading.Lock())
    with lock:
        if host not in ROBOTS:
            rp=RobotFileParser(); rp.set_url(f'{parsed.scheme}://{host}/robots.txt')
            try:
                data,_=raw_get(rp.url,30); rp.parse(data.splitlines()); ROBOTS[host]=rp
            except Exception as exc:
                # A missing robots file is allowed; access-denied / unavailable is not bypassed.
                if getattr(exc,'code',None)==404: ROBOTS[host]=None
                else: raise RuntimeError('robots.txt unavailable: '+str(exc))
        rp=ROBOTS[host]
        if rp and not rp.can_fetch(UA,url): raise RuntimeError('robots.txt disallows this URL')
        delay=max(0.2,min((rp.crawl_delay(UA) or rp.crawl_delay('*') or 0) if rp else 0,30))
        time.sleep(delay)
        for attempt in range(2):
            try: return raw_get(url)
            except Exception as exc:
                if attempt==0 and getattr(exc,'code',None) in (429,500,502,503,504): time.sleep(2); continue
                raise

def json_get(url):
    # These are documented anonymous job-posting APIs, not HTML crawling.
    # api.ashbyhq.com/robots.txt requires auth; /posting-api/job-board does not.
    # Only the explicit public API paths below use this route. Never retry a
    # protected endpoint with alternate credentials or bypass a denied job page.
    p=urlparse(url)
    public=(p.hostname=='api.ashbyhq.com' and p.path.startswith('/posting-api/job-board/'))
    if not public: return json.loads(get(url)[0])
    with GLOBAL_LOCK: lock=LOCKS.setdefault(p.netloc,threading.Lock())
    with lock:
        time.sleep(0.25)
        return json.loads(raw_get(url)[0])
def plain(s): return html.unescape(re.sub('<[^>]+>',' ',str(s or '')))
def slug(s): return re.sub('[^a-z0-9]+','-',s.lower()).strip('-')

def sync_companies(refresh=False):
    path=ROOT/'data/upstream.csv'
    if refresh:
        content,_=raw_get(UPSTREAM)
        candidate=list(csv.DictReader(io.StringIO(content)))
        if len(candidate)<200 or not {'Company','Website','Technology'}.issubset(candidate[0]): raise ValueError('Invalid upstream CSV; retaining previous registry')
        path.write_text(content)
    companies=[]
    for row in csv.DictReader(path.open()):
        website=row['Website'].strip()
        if not website.startswith(('http://','https://')): website='https://'+website
        companies.append({'id':slug(row['Company']), 'name':row['Company'].strip(),'website':website,
                          'category':row['Technology'].strip(),'country':row['Country'].strip(),
                          'in_requested_categories':row['Technology'].strip() in CATEGORIES})
    if len({c['id'] for c in companies})!=len(companies): raise ValueError('Duplicate company IDs')
    write_json(ROOT/'data/companies.json',companies)
    return companies

def board_from_url(url):
    p=urlparse(html.unescape(url).rstrip('\\')); parts=p.path.strip('/').split('/'); host=p.netloc.lower()
    if host.endswith('greenhouse.io'):
        token=parse_qs(p.query).get('for',[None])[0]
        if not token and parts and parts[0] not in ('embed','v1','assets','external_greenhouse_job_boards','ai_opt_out_request'): token=parts[0]
        if token: return {'type':'greenhouse','token':token,'region':'eu' if '.eu.' in host else 'global','url':f'https://{host}/{token}'}
    if host in ('jobs.lever.co','jobs.eu.lever.co') and parts[0]:
        return {'type':'lever','token':parts[0],'region':'eu' if '.eu.' in host else 'global','url':f'https://{host}/{parts[0]}'}
    if host=='jobs.ashbyhq.com' and parts[0]: return {'type':'ashby','token':parts[0],'url':f'https://{host}/{parts[0]}'}
    if host.endswith('.recruitee.com'): return {'type':'recruitee','token':host.split('.')[0],'url':f'https://{host}'}
    if re.search(r'\.jobs\.personio\.(de|com)$',host): return {'type':'personio','token':host,'url':f'https://{host}'}
    return None

def discover(c, previous=None):
    sources={}; pages=[]; errors=[]; visited=set()
    seeds=[c['website']]
    if previous: seeds.extend(previous.get('career_pages',[])[:3])
    queue=list(dict.fromkeys(seeds))
    while queue and len(visited)<6:
        url=queue.pop(0)
        if url in visited: continue
        visited.add(url)
        try:
            body,final=get(url); page=Page(body)
            if url!=c['website'] or CAREER.search(urlparse(final).path): pages.append(final)
            for raw in ATS.findall(html.unescape(body).replace('\\/','/')):
                b=board_from_url(raw.rstrip(').,;'))
                if b: sources[(b['type'],b['token'])]=b
            for href,label in page.links:
                absolute=urldefrag(urljoin(final,href))[0]
                b=board_from_url(absolute)
                if b: sources[(b['type'],b['token'])]=b
                if CAREER.search(label+' '+urlparse(absolute).path) and absolute.startswith(('https://','http://')) and not urlparse(absolute).username:
                    # Follow only career links; no speculative ATS account names.
                    if absolute not in queue and absolute not in visited and len(queue)<8: queue.append(absolute)
        except Exception as exc: errors.append({'url':url,'error':str(exc)[:240]})
    return {'sources':list(sources.values()),'career_pages':list(dict.fromkeys(pages)), 'discovery_errors':errors,'discovered_at':now()}

def make_job(source, id_, title, url, location='',description='',posted=None, employment=''):
    return {'source_id':source,'external_id':str(id_),'title':plain(title).strip(),'url':url,'location':location or 'Not specified',
            'description':plain(description),'employer_posted_at':posted or None,'employment_type':employment or ''}

def fetch_board(b):
    token=b['token']; key=b['type']+':'+token; jobs=[]
    if b['type']=='greenhouse':
        payload=json_get(f'https://boards-api{".eu" if b.get("region")=="eu" else ""}.greenhouse.io/v1/boards/{token}/jobs?content=true')
        if not isinstance(payload.get('jobs'),list): raise ValueError('Missing Greenhouse jobs array')
        for j in payload['jobs']:
            # updated_at is explicitly NOT a publication date.
            jobs.append(make_job(key,j['id'],j['title'],j['absolute_url'],j.get('location',{}).get('name'),j.get('content',''),j.get('first_published')))
    elif b['type']=='lever':
        base='https://api.eu.lever.co' if b.get('region')=='eu' else 'https://api.lever.co'
        skip=0
        while True:
            batch=json_get(f'{base}/v0/postings/{token}?mode=json&limit=100&skip={skip}')
            if not isinstance(batch,list): raise ValueError('Invalid Lever response')
            for j in batch:
                cat=j.get('categories',{}); posted=j.get('createdAt')
                if isinstance(posted,(float,int)): posted=dt.datetime.fromtimestamp(posted/1000,dt.timezone.utc).isoformat()
                jobs.append(make_job(key,j['id'],j['text'],j['hostedUrl'],cat.get('location'),j.get('descriptionPlain',''),posted,cat.get('commitment','')))
            if len(batch)<100: break
            skip+=100
            if skip>20000: raise ValueError('Lever pagination limit exceeded')
    elif b['type']=='ashby':
        payload=json_get(f'https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true')
        if not isinstance(payload.get('jobs'),list): raise ValueError('Missing Ashby jobs array')
        for j in payload['jobs']:
            if j.get('isListed',True): jobs.append(make_job(key,j['id'],j['title'],j['jobUrl'],'; '.join(filter(None,[j.get('location')]+[x.get('location') for x in j.get('secondaryLocations',[])])),j.get('descriptionPlain',''),j.get('publishedAt'),j.get('employmentType','')))
    elif b['type']=='recruitee':
        payload=json_get(f'https://{token}.recruitee.com/api/offers/')
        if not isinstance(payload.get('offers'),list): raise ValueError('Missing Recruitee offers array')
        for j in payload['offers']: jobs.append(make_job(key,j['id'],j['title'],j['careers_url'],j.get('location'),j.get('description',''),j.get('published_at'),j.get('employment_type_code','')))
    elif b['type']=='personio':
        payload=ET.fromstring(get(f'https://{token}/xml?language=en')[0])
        if payload.tag!='workzag-jobs': raise ValueError('Invalid Personio XML job feed')
        for j in payload.findall('position'):
            jid=j.findtext('id'); title=j.findtext('name')
            if not jid or not title: raise ValueError('Invalid Personio position')
            jobs.append(make_job(key,jid,title,f'https://{token}/job/{jid}?language=en',j.findtext('office'), ' '.join(j.itertext()),None,j.findtext('employmentType') or ''))
    else: raise ValueError('Unsupported board')
    if any(not j['title'] or not j['url'] for j in jobs): raise ValueError('Invalid job record')
    return jobs

def walk_json(value):
    if isinstance(value,dict):
        if value.get('@type')=='JobPosting' or 'JobPosting' in (value.get('@type') or []): yield value
        for v in value.values(): yield from walk_json(v)
    elif isinstance(value,list):
        for v in value: yield from walk_json(v)

def fetch_generic(url, company=None):
    body,final=get(url); page=Page(body); jobs=[]
    for blob in page.jsonld:
        for j in walk_json(blob):
            expiry=j.get('validThrough')
            if expiry:
                try:
                    expires=dt.datetime.fromisoformat(expiry.replace('Z','+00:00'))
                    if expires.tzinfo is None: expires=expires.replace(tzinfo=dt.timezone.utc)
                    if expires<dt.datetime.now(dt.timezone.utc): continue
                except ValueError: continue
            # Aggregators can contain unrelated suggested jobs: keep company-scoped URLs only.
            candidate=urljoin(final,j.get('url') or final)
            if 'ycombinator.com/companies/' in final:
                prefix=final.split('/jobs')[0]
                if not candidate.startswith(prefix+'/jobs/'): continue
            loc=j.get('jobLocation',[]); loc=loc if isinstance(loc,list) else [loc]
            location='; '.join(', '.join(str(v) for k,v in l.get('address',{}).items() if isinstance(v,str) and not k.startswith('@')) for l in loc if isinstance(l,dict) and isinstance(l.get('address',{}),dict))
            job_url=urljoin(final,j.get('url') or final)
            jobs.append(make_job('page:'+url,job_url,j.get('title',''),job_url,location,j.get('description',''),j.get('datePosted'),str(j.get('employmentType',''))))
    # Unstructured links are leads, never presented as verified open jobs.
    leads=[]
    for href,label in page.links:
        target=urldefrag(urljoin(final,href))[0]; title=plain(label).strip()
        if not INTERN.search(title) or NONTECH.search(title) or not target.startswith(('http://','https://')): continue
        if 'ycombinator.com/companies/' in final and not target.startswith(final.split('/jobs')[0]+'/jobs/'): continue
        if urlparse(target).hostname in ('www.teamtailor.com','teamtailor.com'): continue
        if len(title)>180 or re.search(r'hear from|story|stories|blog|news|experience|life at',title,re.I): continue
        leads.append({'title':title,'url':target})
    return jobs,leads

def qualifies(j):
    return bool(INTERN.search(j['title']+' '+j.get('employment_type',''))) and not NONTECH.search(j['title'])

def annotate(j,c):
    text=j['title']+' '+j.get('description','')
    j['company']=c['name']; j['company_id']=c['id']; j['category']=c['category']
    j['fit_tags']=[k for k,pattern in FIT.items() if re.search(pattern,text,re.I)]
    j['term']='; '.join(dict.fromkeys(re.findall(r'(?i)\b(?:summer|fall|autumn|spring|winter)\s*20\d{2}\b',text))) or 'Not specified'
    j['advanced_degree']='Check requirements' if re.search(r'\b(Ph\.?D|doctoral|masters?|master\x27s)\b',text,re.I) else 'Not flagged'
    j['id']=hashlib.sha256((c['id']+'|'+j['source_id']+'|'+j['external_id']).encode()).hexdigest()[:20]
    j.pop('description',None)
    return j

def scan_company(c, old, rediscover=False):
    stale=not old.get('sources') or not old.get('discovered_at') or (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(old['discovered_at'])).days>=7
    d=discover(c,old) if rediscover or stale else {k:old.get(k,[]) for k in ['sources','career_pages','discovery_errors'] } | {'discovered_at':old['discovered_at']}
    overrides=read_json(ROOT/'data/source_overrides.json',{}).get(c['id'],{})
    if overrides.get('sources') is not None: d['sources']=overrides['sources']
    d['career_pages']=list(dict.fromkeys(urldefrag(u)[0] for u in d['career_pages']+overrides.get('career_pages',[]) if 'googletagmanager.com' not in u))
    # Migrate cached sources through the improved parser, dropping asset URLs.
    normalized={}
    for b in d['sources']:
        parsed=board_from_url(b['url'])
        if parsed: normalized[(parsed['type'],parsed['token'])]=parsed
    for u in d['career_pages']:
        parsed=board_from_url(u)
        if parsed: normalized[(parsed['type'],parsed['token'])]=parsed
    d['sources']=list(normalized.values())
    jobs=[]; success=[]; errors=[]; leads=[]
    for b in d['sources']:
        key=b['type']+':'+b['token']
        try: jobs.extend(fetch_board(b)); success.append(key)
        except Exception as exc: errors.append({'source':key,'url':b['url'],'error':str(exc)[:240]})
    # A source API is complete for its board. Generic pages remain partial even when fetched.
    if not success:
        for url in d['career_pages'][:3]:
            try:
                js,ls=fetch_generic(url,c); jobs.extend(js); leads.extend(ls)
            except Exception as exc: errors.append({'source':'page:'+url,'url':url,'error':str(exc)[:240]})
    verified_urls={j['url'] for j in jobs}
    unique_leads={l['url']:l for l in leads if l['url'] not in verified_urls}
    # Follow promising internship detail links, not only the listing landing page.
    if not success:
        for url in list(unique_leads)[:15]:
            if url in d['career_pages']: continue
            try:
                js,_=fetch_generic(url,c); jobs.extend(js)
                for j in js: unique_leads.pop(j['url'],None)
            except Exception as exc: errors.append({'source':'page:'+url,'url':url,'error':str(exc)[:240]})
    leads=list(unique_leads.values())
    d.update({'company_id':c['id'],'checked_at':now(),'successful_sources':success,'errors':errors,'leads':leads,
              'coverage':'API checked' if success and not errors else ('Partial / needs review' if d['career_pages'] or success else 'Discovery blocked / needs review'),
              'total_board_jobs':len(jobs)})
    return d,[annotate(j,c) for j in jobs if qualifies(j)]

def reconcile(old_jobs,found,reports,stamp):
    jobs={j['id']:dict(j) for j in old_jobs}; events=[]; seen=set()
    def event(j,kind): events.append({'at':stamp,'event':kind,'job_id':j['id'],'company':j['company'],'title':j['title'],'url':j['url']})
    for j in found:
        jid=j['id']; seen.add(jid); previous=jobs.get(jid)
        if previous:
            j['first_seen_at']=previous['first_seen_at']
            if previous.get('status')=='closed': event(j,'reopened')
            elif any(j.get(k)!=previous.get(k) for k in ['title','location','url','term']): event(j,'updated')
        else: j['first_seen_at']=stamp; event(j,'new')
        j.update(status='open',last_seen_at=stamp,last_checked_at=stamp,missing_checks=0,closed_at=None)
        jobs[jid]=j
    by_company={r['company_id']:r for r in reports}
    for jid,j in jobs.items():
        if jid in seen or j.get('status')=='closed': continue
        report=by_company.get(j['company_id'],{})
        if j['source_id'] in report.get('successful_sources',[]):
            last=j.get('last_checked_at')
            # Require two successful absences separated by at least six hours.
            if not last or (dt.datetime.fromisoformat(stamp)-dt.datetime.fromisoformat(last)).total_seconds()>=21600:
                j['missing_checks']=j.get('missing_checks',0)+1; j['last_checked_at']=stamp
            if j.get('missing_checks',0)>=2: j.update(status='closed',closed_at=stamp); event(j,'closed')
            else: j['status']='unconfirmed'
        else: j['status']='unconfirmed'  # Failed/partial scans never close a role.
    # Consolidate shared boards listed under company aliases (e.g. a rebrand).
    # Preserve all names as aliases while keeping one row per source job.
    canonical={}
    for j in jobs.values():
        key=(j['source_id'],j['external_id'])
        if key not in canonical: canonical[key]=j; continue
        previous=canonical[key]
        if j['first_seen_at']<previous['first_seen_at']: canonical[key]=j; j,previous=previous,j
        previous['company_aliases']=sorted(set(previous.get('company_aliases',[])+[j['company']]))
    kept=list(canonical.values()); kept_ids={j['id'] for j in kept}
    events=[e for e in events if e['job_id'] in kept_ids]
    return sorted(kept,key=lambda j:(j['company'].lower(),j['title'])),events

def cell(value): return str(value or '—').replace('|','\\|').replace('\n',' ').replace('<','&lt;').replace('>','&gt;')
def link(label,url): return '['+cell(label).replace('[','\\[').replace(']','\\]')+']('+url.replace(' ','%20').replace(')','%29')+')'
def table(headers,rows): return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(cell(x) for x in row)+' |' for row in rows])

def render(companies,jobs,reports,stamp):
    opened=[j for j in jobs if j['status']=='open']; byid={r['company_id']:r for r in reports}
    complete=sum(r['coverage']=='API checked' for r in reports)
    lines=['# Semiconductor Startup Internships','', '**US + international · all internship terms · technical roles**','',
    'A GitHub internship log inspired by [SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships), using the company universe from [Andreas Olofsson’s semiconductor startup database](https://github.com/aolofsson/awesome-semiconductor-startups).','',
    f'Latest scan: **{stamp}** · **{len(companies)} companies registered** · **{complete} with successful complete board API checks** · **{len(opened)} open technical internships found**.','',
    '**Coverage is not exhaustive.** Every company is registered and discovery is attempted. Unsupported, inaccessible, JavaScript-only and unstructured career pages remain in the [coverage report](COVERAGE.md). A successful API check covers that board, not every possible company source. Unverified internship links are in [leads](LEADS.md).','',
    '[US locations](US.md) · [International / other locations](INTERNATIONAL.md) · [All companies / coverage](COVERAGE.md) · [Change log](CHANGELOG.md) · [Closed / unconfirmed](ARCHIVE.md) · [Setup](SETUP.md)','',
    '## Dates and status','',
    '- **Posted** is the employer-supplied date, where available. It may reflect republication. “Unknown” means the source does not expose it; update timestamps are never substituted.',
    '- **First found** is when this tracker first saw the job; it is not a release date.',
    '- **Open** means present in a successfully fetched public listing. Always confirm eligibility and availability on the application page.',
    '- Failed checks make previous roles unconfirmed. Closure requires two successful complete-board absences at least six hours apart.',
    '- Relevance tags are keyword hints based on hardware/debug/RTL/physical-design/embedded/software interests, not eligibility judgments. All technical internships are retained, including graduate roles.',
    '- No resume, contact details, or application documents are included.','', '## Open internships','']
    headers=['Company','Role / apply','Location','Term','Posted','First found','Relevance']
    def rows(items): return [[j['company'],link(j['title'],j['url']),j['location'],j['term'],(j.get('employer_posted_at') or 'Unknown')[:10],j['first_seen_at'][:10],', '.join(j['fit_tags']) or 'Explore'] for j in sorted(items,key=lambda j:(j['first_seen_at'],j['company']),reverse=True)]
    for cat in CATEGORIES+sorted({c['category'] for c in companies}-set(CATEGORIES)):
        entries=[j for j in opened if j['category']==cat]
        lines+=['### '+cat+(' (additional category)' if cat not in CATEGORIES else ''),'',table(headers,rows(entries)) if entries else '_No verified matching openings found in successfully checked sources. See coverage before interpreting this._','']
    (ROOT/'README.md').write_text('\n'.join(lines))
    coverage=[]; leadrows=[]
    for c in companies:
        r=byid.get(c['id'],{}); urls=[b['url'] for b in r.get('sources',[])]+r.get('career_pages',[])
        err=r.get('errors',[])+r.get('discovery_errors',[])
        coverage.append([link(c['name'],c['website']),c['category'],c['country'],r.get('coverage','Not checked'),'; '.join(link('Source',u) for u in dict.fromkeys(urls)),r.get('checked_at','—'),'; '.join(x['error'] for x in err)[:350]])
        for l in r.get('leads',[]): leadrows.append([c['name'],link(l['title'],l['url']),'Unverified link; may be an old posting or general program'])
    (ROOT/'COVERAGE.md').write_text('# Company coverage\n\nEvery upstream startup is listed. Company country is headquarters, not job location. API checked means a supported public board was retrieved; it is not a guarantee of all-company coverage. Errors remain visible.\n\n'+table(['Company','Category','HQ','Coverage','Careers / boards','Checked UTC','Issues'],coverage)+'\n')
    (ROOT/'LEADS.md').write_text('# Internship leads requiring verification\n\nThese are not counted as open jobs.\n\n'+table(['Company','Link','Status'],leadrows)+'\n')
    (ROOT/'ARCHIVE.md').write_text('# Closed and unconfirmed\n\n'+table(['Company','Role','Status','First found','Last seen','Closed at'],[[j['company'],link(j['title'],j['url']),j['status'],j['first_seen_at'],j['last_seen_at'],j.get('closed_at')] for j in jobs if j['status']!='open'])+'\n')
    us=re.compile(r'\b(united states|usa|u\.s\.|california|texas|new york|massachusetts|oregon|washington|pennsylvania|colorado|arizona|georgia|san francisco|san jose|santa clara|austin|boston|pittsburgh|seattle|cupertino|sunnyvale|mountain view|san diego|durham|raleigh|portland|boulder|cambridge,? ma)\b|,\s*(CA|TX|MA|NY|WA|OR|PA|CO|AZ|GA|NC)\b',re.I)
    for filename,title,items in [('US.md','US location matches',[j for j in opened if us.search(j['location'])]),('INTERNATIONAL.md','International / other / unspecified locations',[j for j in opened if not us.search(j['location'])])]:
        (ROOT/filename).write_text('# '+title+'\n\nLocation keyword grouping only. Multi-country roles may appear in US results; remote eligibility is not inferred. Consult the full README and employer listing.\n\n'+table(headers,rows(items))+'\n')
    events=read_json(ROOT/'data/events.json',[])
    (ROOT/'CHANGELOG.md').write_text('# Internship change log\n\nEvents use observation time in UTC, not employer publication time.\n\n'+table(['Observed UTC','Event','Company','Role'],[[e['at'],e['event'],e['company'],link(e['title'],e['url'])] for e in reversed(events)])+'\n')
    with (ROOT/'data/internships.csv').open('w',newline='') as f:
        fields=['company','category','title','location','term','status','url','employer_posted_at','first_seen_at','last_seen_at','fit_tags']
        writer=csv.DictWriter(f,fields,extrasaction='ignore'); writer.writeheader()
        for j in jobs: writer.writerow(j|{'fit_tags':'; '.join(j['fit_tags'])})

def main():
    p=argparse.ArgumentParser(); p.add_argument('--refresh-companies',action='store_true'); p.add_argument('--rediscover',action='store_true'); p.add_argument('--workers',type=int,default=12); p.add_argument('--limit',type=int); p.add_argument('--render-only',action='store_true'); args=p.parse_args()
    companies=sync_companies(args.refresh_companies)
    old=read_json(ROOT/'data/coverage.json',[]); oldmap={r['company_id']:r for r in old}
    if args.render_only:
        render(companies,read_json(ROOT/'data/jobs.json',[]),old,now()); return
    selected=companies[:args.limit] if args.limit else companies; reports=[]; found=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(scan_company,c,oldmap.get(c['id'],{}),args.rediscover):c for c in selected}
        for future in concurrent.futures.as_completed(futures):
            c=futures[future]
            try: report,jobs=future.result()
            except Exception as exc:
                report=oldmap.get(c['id'],{})|{'company_id':c['id'],'checked_at':now(),'coverage':'Discovery blocked / needs review','successful_sources':[],'errors':[{'error':str(exc)}]}; jobs=[]
            reports.append(report); found.extend(jobs)
            print(f'{len(reports)}/{len(selected)} {c["name"]}: {report["coverage"]}; {len(jobs)} internships',flush=True)
            # Checkpoint discovery results to survive an interrupted first scan.
            oldmap[c['id']]=report; write_json(ROOT/'data/coverage.json',list(oldmap.values()))
            write_json(ROOT/'data/scan_found.json',found)
    stamp=now(); jobs,events=reconcile(read_json(ROOT/'data/jobs.json',[]),found,reports,stamp)
    allreports=list(oldmap.values()); write_json(ROOT/'data/jobs.json',jobs)
    write_json(ROOT/'data/events.json',read_json(ROOT/'data/events.json',[])+events)
    render(companies,jobs,allreports,stamp)
    write_json(ROOT/'data/last_run.json',{'at':stamp,'companies_attempted':len(selected),'api_checked':sum(r['coverage']=='API checked' for r in reports),'open_internships':sum(j['status']=='open' for j in jobs),'events':len(events)})
    print(json.dumps(read_json(ROOT/'data/last_run.json',{})))

if __name__=='__main__': main()
