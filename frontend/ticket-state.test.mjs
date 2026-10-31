import {build} from 'esbuild';
import assert from 'node:assert/strict';
const result=await build({entryPoints:['frontend/ticket-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {mergeSubmittedTicket,receiptReady,customerStatus}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
const confirmed={id:'request-one',reference:'HOS-existing-job',deliveryStatus:'delivered',status:'000 - Intake'};
const pending={...confirmed,deliveryStatus:'pending',status:'Saved'};
assert.deepEqual(mergeSubmittedTicket([confirmed],pending),[confirmed]);
assert.equal(mergeSubmittedTicket([pending],confirmed).length,1);
assert.equal(mergeSubmittedTicket([{...confirmed,id:'another-request'}],confirmed).length,2);
console.log('Ticket merge: duplicate race, confirmed status, and repeat-customer requests passed.');

assert.equal(receiptReady(pending),false);
assert.equal(receiptReady({...confirmed,reference:null}),false);
assert.equal(receiptReady(confirmed),true);
console.log("Receipt waits for confirmed delivery and assigned number.");

const statuses={'000':'Ticket Received','010':'Reviewing Ticket','020':'Contacted','023':'Pending','024':'Requested Replacement','025':'Scheduled','030':'Completed','031':'Completed by Phone','032':'Completed by Text','040':'Follow up','050':'Under Review by Service Team','051':'Resolution in Progress'};
for(const [code,label] of Object.entries(statuses)) {
 for(const raw of [code,code+' - Internal wording',code+'-Internal wording',' '+code+' – Internal wording ']) assert.equal(customerStatus(raw),label);
}
assert.equal(customerStatus('000 - Tesla Concern Created'),'Ticket Received');
assert.equal(customerStatus('999 - Other'),'999 - Other');
assert.equal(customerStatus(''),'Status unavailable');
assert.equal(customerStatus('0300 - Other'),'0300 - Other');
console.log('All 12 customer status mappings and unknown-status fallback passed.');
