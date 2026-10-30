import {build} from 'esbuild';
import assert from 'node:assert/strict';
const result=await build({entryPoints:['frontend/ticket-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {mergeSubmittedTicket,receiptReady}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
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
