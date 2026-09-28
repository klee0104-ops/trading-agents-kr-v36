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
  console.log('Frontend regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
