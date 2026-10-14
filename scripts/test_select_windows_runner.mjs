import test from 'node:test';
import assert from 'node:assert/strict';
import { selectRunner, resolveRunner } from './select-windows-runner.mjs';
const ready = {name:'windows-book',status:'online',busy:false,labels:[{name:'windows-book'}]};
test('auto prefers the exact available machine', () => assert.equal(selectRunner('auto',[ready]),'windows-book'));
for (const [name, runners] of [ ['absent',[]],['offline',[{...ready,status:'offline'}]],
  ['busy',[{...ready,busy:true}]],['wrong name',[{...ready,name:'other'}]],['wrong label',[{...ready,labels:[]}]] ]) {
  test(`auto falls back when ${name}`, () => assert.equal(selectRunner('auto',runners),'windows-2022'));
}
test('explicit modes override availability', () => {
  assert.equal(selectRunner('hosted',[ready]),'windows-2022');
  assert.equal(selectRunner('self-hosted',[]),'windows-book');
  assert.throws(() => selectRunner('invalid'),/Invalid/);
});
test('missing credentials, permissions, malformed data and network errors fall back', async () => {
  assert.equal(await resolveRunner('auto','','a/b',()=>assert.fail()),'windows-2022');
  for (const fetcher of [async()=>({ok:false}),async()=>{throw Error('network');},async()=>({ok:true,json:async()=>({})})])
    assert.equal(await resolveRunner('auto','secret','a/b',fetcher),'windows-2022');
});
test('successful inventory resolves self-hosted', async () => {
  assert.equal(await resolveRunner('auto','secret','a/b',async()=>({ok:true,json:async()=>({runners:[ready]})})),'windows-book');
});
