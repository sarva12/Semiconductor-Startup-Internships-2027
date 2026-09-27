import sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tracker as t

class TrackerTests(unittest.TestCase):
    def job(self):
        return {'id':'1','company_id':'c','company':'Company','title':'RTL Intern','source_id':'greenhouse:c','external_id':'1','url':'https://example.com/1','location':'US','term':'Summer 2027','fit_tags':[]}
    def initial(self): return t.reconcile([], [self.job()], [], '2026-09-27T00:00:00+00:00')[0]
    def test_failed_scan_never_closes(self):
        jobs,events=t.reconcile(self.initial(),[],[{'company_id':'c','successful_sources':[]}],'2026-09-28T00:00:00+00:00')
        self.assertEqual(jobs[0]['status'],'unconfirmed'); self.assertEqual(events,[])
    def test_closure_requires_two_spaced_successes(self):
        r=[{'company_id':'c','successful_sources':['greenhouse:c']}]
        jobs,_=t.reconcile(self.initial(),[],r,'2026-09-27T06:00:00+00:00')
        self.assertEqual(jobs[0]['status'],'unconfirmed')
        jobs,_=t.reconcile(jobs,[],r,'2026-09-27T06:01:00+00:00')
        self.assertEqual(jobs[0]['missing_checks'],1)
        jobs,events=t.reconcile(jobs,[],r,'2026-09-27T12:00:00+00:00')
        self.assertEqual(jobs[0]['status'],'closed'); self.assertEqual(events[0]['event'],'closed')
    def test_first_found_survives_reopen(self):
        jobs=self.initial(); jobs[0]['status']='closed'
        result,events=t.reconcile(jobs,[self.job()],[],'2026-10-01T00:00:00+00:00')
        self.assertEqual(result[0]['first_seen_at'],'2026-09-27T00:00:00+00:00'); self.assertEqual(events[0]['event'],'reopened')
    def test_fulltime_mentioning_interns_not_included(self):
        self.assertFalse(t.qualifies({'title':'Senior ASIC Engineer','description':'Mentor interns'}))
        self.assertTrue(t.qualifies({'title':'ASIC Intern'}))
        self.assertFalse(t.qualifies({'title':'Marketing Intern'}))
        self.assertTrue(t.qualifies({'title':'FPGA Engineer','employment_type':'Internship'}))
    def test_greenhouse_updated_is_not_posted(self):
        with patch.object(t,'json_get',return_value={'jobs':[{'id':1,'title':'RTL Intern','absolute_url':'https://example.com/1','updated_at':'2026-09-25'}]}):
            job=t.fetch_board({'type':'greenhouse','token':'c'})[0]
        self.assertIsNone(job['employer_posted_at'])
    def test_invalid_board_response_is_error(self):
        with patch.object(t,'json_get',return_value={'error':'unavailable'}):
            with self.assertRaises(ValueError): t.fetch_board({'type':'greenhouse','token':'c'})
    def test_lever_pagination(self):
        row={'id':'1','text':'Intern','hostedUrl':'https://example.com/1','categories':{}}
        with patch.object(t,'json_get',side_effect=[[row]*100,[]]) as fetch:
            self.assertEqual(len(t.fetch_board({'type':'lever','token':'c'})),100)
            self.assertIn('skip=100',fetch.call_args.args[0])
    def test_board_detection(self):
        self.assertEqual(t.board_from_url('https://boards.greenhouse.io/embed/job_board?for=company')['token'],'company')
        self.assertEqual(t.board_from_url('https://jobs.eu.lever.co/company/id')['region'],'eu')
        self.assertIsNone(t.board_from_url('https://example.com/company'))
    def test_html_links_and_jsonld(self):
        p=t.Page('<a href="/jobs/1">ASIC Intern</a><script type="application/ld+json">{"@type":"JobPosting","title":"Intern"}</script>')
        self.assertEqual(p.links,[('/jobs/1','ASIC Intern')]); self.assertEqual(len(list(t.walk_json(p.jsonld))),1)

    def test_asset_urls_are_not_boards(self):
        self.assertIsNone(t.board_from_url('https://job-boards.greenhouse.io/assets/logo.svg'))
        self.assertIsNone(t.board_from_url('https://job-boards.greenhouse.io/external_greenhouse_job_boards/main.js'))
        self.assertEqual(t.board_from_url('https://job-boards.eu.greenhouse.io/fractile')['region'],'eu')
    def test_tracking_iframe_not_a_career_link(self):
        p=t.Page('<iframe src="https://www.googletagmanager.com/ns.html?id=1"></iframe>')
        self.assertEqual(p.links,[])
    def test_personio_feed(self):
        xml='<workzag-jobs><position><id>123</id><name>FPGA Intern</name><office>Dresden</office><employmentType>intern</employmentType></position></workzag-jobs>'
        with patch.object(t,'get',return_value=(xml,'https://example.jobs.personio.de/xml')):
            jobs=t.fetch_board({'type':'personio','token':'example.jobs.personio.de'})
        self.assertEqual(jobs[0]['title'],'FPGA Intern')
        self.assertTrue(t.qualifies(jobs[0]))
    def test_expired_structured_job_ignored(self):
        html='<script type="application/ld+json">{"@type":"JobPosting","title":"Intern","validThrough":"2020-01-01","url":"https://example.com/job"}</script>'
        with patch.object(t,'get',return_value=(html,'https://example.com/job')):
            self.assertEqual(t.fetch_generic('https://example.com/job')[0],[])
    def test_shared_board_alias_dedup(self):
        a=self.job(); b=self.job()|{'id':'2','company':'Other alias','company_id':'other'}
        jobs,events=t.reconcile([],[a,b],[],'2026-09-27T00:00:00+00:00')
        self.assertEqual(len(jobs),1); self.assertEqual(len(events),1)
        self.assertEqual(jobs[0]['company_aliases'],['Other alias'])

if __name__=='__main__': unittest.main()
