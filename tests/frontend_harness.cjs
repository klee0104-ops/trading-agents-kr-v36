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
  vm.runInContext(`lastOutcome={final_result:{kospi_prediction:{direction:'up',outlook_label:'<img src=x>',confidence:'<b>bad</b>'}}}; renderKPI();`, sandbox);
  assert.ok(!nodes.get('#kpiRow').innerHTML.includes('<img'));
  assert.match(nodes.get('#kpiRow').innerHTML,/&lt;img/);
  vm.runInContext('renderPrediction()',sandbox);
  assert.match(nodes.get('#predictionCard').innerHTML,/미검증/);
  vm.runInContext('renderQA()',sandbox);
  assert.match(nodes.get('#qaBox').innerHTML,/미검증/);
  assert.ok(!nodes.get('#qaBox').innerHTML.includes('규칙 통과'));
  const reportHtml=vm.runInContext(`reportLibraryView({total:1,reports:[{team:'short',id:'synthetic',at:'2026-09-29T08:00:00',target_date:'2026-09-29',title:'<img src=x>',prediction:{direction:'up',up_probability:60},roles:[{name:'tester',summary:'<script>bad</script>',points:[]}],debate:[],stocks:[],sources:[]}]},escapeHtml)`,sandbox);
  assert.ok(!reportHtml.includes('<img') && !reportHtml.includes('<script>'));
  assert.match(reportHtml,/&lt;img/);
  assert.match(reportHtml,/<details class="report-detail">/);
  assert.match(reportHtml,/미기록/);
  assert.equal(vm.runInContext("reportVerdict({direction:'down',up_probability:38})",sandbox),'하락 예상');
  assert.equal(vm.runInContext("reportVerdict({direction:'up',up_probability:62})",sandbox),'상승 예상');
  assert.equal(vm.runInContext("reportVerdict({direction:'flat',up_probability:48})",sandbox),'보합권 · 하락 쪽 우세');
  assert.equal(vm.runInContext("reportVerdict({direction:'flat',up_probability:50})",sandbox),'보합 예상 · 뚜렷한 우세 없음');
  assert.match(vm.runInContext("reportVerdict({direction:'down',up_probability:80})",sandbox),/판단 불일치/);
  assert.match(reportHtml,/본문 보기/);
  assert.ok(reportHtml.indexOf('report-preview') < reportHtml.indexOf('<details'));
  console.log('Frontend regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
