const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
function element() { return {innerHTML:'',textContent:'',className:'',style:{},disabled:false}; }
const sandbox = {
  document: {querySelector: s => {if(!nodes.has(s)) nodes.set(s,element()); return nodes.get(s);}, addEventListener(){}},
  location: {href:'http://127.0.0.1:5000/'}, console, URL,
  fetch: async () => ({ok:true,json:async()=>({summary:{},meta:{},days:[],runs:[
    {file:'a.json',meta:{},summary:{total:2,correct:1}},
    {file:'b.json',meta:{},summary:{total:2,correct:2}}
  ]})})
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('web/static/app.js','utf8'),sandbox);
(async()=>{
  await vm.runInContext('loadBacktest()',sandbox);
  assert.match(nodes.get('#backtestBox').innerHTML,/버전 비교/);
  assert.match(nodes.get('#backtestBox').innerHTML,/1\/2 \(50%\)/);
  assert.equal(vm.runInContext("kstIso(new Date('2026-10-01T00:00:00Z'))",sandbox),'2026-10-01T09:00:00');
  vm.runInContext(`lastOutcome={final_result:{kospi_prediction:{direction:'up',outlook_label:'<img src=x>',confidence:'<b>bad</b>'}}}; renderKPI();`, sandbox);
  assert.ok(!nodes.get('#kpiRow').innerHTML.includes('<img'));
  assert.match(nodes.get('#kpiRow').innerHTML,/&lt;img/);
  vm.runInContext('renderPrediction()',sandbox);
  assert.match(nodes.get('#predictionCard').innerHTML,/미검증/);
  assert.match(nodes.get('#predictionCard').innerHTML,/data-confidence-trigger/);
  assert.match(nodes.get('#predictionCard').innerHTML,/45% 이상 ~ 55% 미만/);
  assert.match(nodes.get('#predictionCard').innerHTML,/과거 적중률이나 모의투자 수익률을 뜻하지 않습니다/);
  assert.match(nodes.get('#predictionCard').innerHTML,/&lt;b&gt;bad&lt;\/b&gt;/);
  vm.runInContext(`{
    const today = kstIso(new Date()).slice(0,10);
    kospiSeries = {dates:['2026-09-30',today],closes:[100,102]};
    liveData = {status:{code:'open'},rows:[{name:'KOSPI',date:today,is_today:true,value:105,prev:103}]};
    renderKospiChart();
  }`, sandbox);
  assert.equal(nodes.get('#kPx').textContent, '105.00');
  assert.match(nodes.get('#kDate').textContent,/장중 시세 · 미확정/);
  assert.match(nodes.get('#kChg').textContent,/1\.94%/);
  vm.runInContext(`liveData={rows:[{name:'KOSPI',date:'2020-01-01',is_today:true,value:999,prev:100}]};renderKospiChart();`,sandbox);
  assert.equal(nodes.get('#kPx').textContent, '102.00');
  assert.match(nodes.get('#kDate').textContent,/종가/);
  const qaNotice=vm.runInContext("paperActivityView({market:'장중',short_reason:'보류',etf_qa_reason:'<img src=x>',long_reason:'현금'},escapeHtml)",sandbox);
  assert.match(qaNotice,/ETF QA 보류 사유/);
  assert.match(qaNotice,/&lt;img src=x&gt;/);
  assert.ok(!qaNotice.includes('<img'));
  vm.runInContext('renderQA()',sandbox);
  assert.match(nodes.get('#qaBox').innerHTML,/미검증/);
  assert.ok(!nodes.get('#qaBox').innerHTML.includes('규칙 통과'));
  const reportHtml=vm.runInContext(`reportLibraryView({total:1,reports:[{team:'short',id:'synthetic',at:'2026-09-29T08:00:00',target_date:'2026-09-29',title:'<img src=x>',prediction:{direction:'up',up_probability:60},roles:[{name:'tester',summary:'<script>bad</script>',points:[]}],debate:[],stocks:[],sources:[]}]},escapeHtml)`,sandbox);
  assert.ok(!reportHtml.includes('<img') && !reportHtml.includes('<script>'));
  assert.match(reportHtml,/&lt;img/);
  assert.match(reportHtml,/<details class="report-detail">/);
  assert.match(reportHtml,/미기록/);
  const featured=vm.runInContext("companyListRows({companies:[{code:'A00001',name:'첫째'},{code:'B00002',name:'둘째'}],featured_codes:['B00002','A00001'],reports:[]},'').map(x=>x.code).join(',')",sandbox);
  assert.equal(featured,'B00002,A00001');
  const searched=vm.runInContext("companyListRows({companies:[{code:'A00001',name:'첫째'},{code:'B00002',name:'둘째'}],featured_codes:['B00002']},'A00001')[0].code",sandbox);
  assert.equal(searched,'A00001');
  assert.equal(vm.runInContext("reportVerdict({direction:'down',up_probability:38})",sandbox),'하락 예상');
  assert.equal(vm.runInContext("reportVerdict({direction:'up',up_probability:62})",sandbox),'상승 예상');
  assert.equal(vm.runInContext("reportVerdict({direction:'flat',up_probability:48})",sandbox),'보합권 · 하락 쪽 우세');
  assert.equal(vm.runInContext("reportVerdict({direction:'flat',up_probability:50})",sandbox),'보합 예상 · 뚜렷한 우세 없음');
  assert.match(vm.runInContext("reportVerdict({direction:'down',up_probability:80})",sandbox),/판단 불일치/);
  assert.match(reportHtml,/본문 보기/);
  assert.ok(reportHtml.indexOf('report-preview') < reportHtml.indexOf('<details'));
  const noLongBuy=vm.runInContext("longPreopenView({status:'approved',allocations:[],generated_at:'2026-10-01T00:38:45+09:00'},escapeHtml)",sandbox);
  assert.match(noLongBuy,/승인된 종목별 배분이 0건/);
  assert.doesNotMatch(noLongBuy,/조건부 매수 후보/);
  assert.match(noLongBuy,/현금 보유의 0% 수익률을 예측 성능으로 해석하지 마세요/);
  const missingLong=vm.runInContext("longPreopenView({status:'approved',allocations:[],candidates:[{name:'<img src=x>',missing:['분기 EPS 누락'],gate:{phase:'data_wait'}}]},escapeHtml)",sandbox);
  assert.match(missingLong,/자료 확인 대기 1곳/);
  assert.match(missingLong,/분기 EPS 누락/);
  assert.doesNotMatch(missingLong,/<img/);
  const heldLong=vm.runInContext("longPreopenView({status:'held',allocations:[{code:'005930',weight:0.2,buy_below:38000}]},escapeHtml)",sandbox);
  assert.match(heldLong,/신규 매수 보류/);
  assert.doesNotMatch(heldLong,/38,000원/);
  const conditionalLong=vm.runInContext("longPreopenView({status:'approved',allocations:[{code:'<img src=x>',weight:0.2,buy_below:38000}]},escapeHtml)",sandbox);
  assert.match(conditionalLong,/38,000원 이하/);
  assert.match(conditionalLong,/체결 예정가가 아닌/);
  assert.match(conditionalLong,/목표 매도가는 현재 계획에 없습니다/);
  assert.doesNotMatch(conditionalLong,/<img/);
  console.log('Frontend regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
