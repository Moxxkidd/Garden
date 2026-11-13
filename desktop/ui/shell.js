const $=id=>document.getElementById(id);
const invoke=(action,payload={})=>window.__TAURI__.core.invoke('shell_action',{action,payload});
const show=value=>{$('output').textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
async function run(button,action,payload={}){button.disabled=true;try{const result=await invoke(action,payload);show(result);return result;}catch(error){show(String(error));throw error;}finally{button.disabled=false;}}
function bind(id,fn){$(id).addEventListener('click',()=>Promise.resolve(fn($(id))).catch(()=>{}));}
const home=()=>({home:$('home').value.trim()});
bind('choose',async b=>{const path=await run(b,'choose-directory');if(path)$('home').value=path;});
bind('inspect',b=>run(b,'inspect',home()));
bind('connect',b=>run(b,'connect',{...home(),initialize:$('initialize').checked}));
bind('upgrade',async b=>{if(!$('upgrade-consent').checked){show('请先明确确认备份并升级。');return;}await run(b,'upgrade',{...home(),confirmed:true});$('upgrade-consent').checked=false;});
bind('diagnose',b=>run(b,'diagnose'));
bind('example',b=>run(b,'example'));
$('notifications').addEventListener('change',()=>invoke('notifications',{enabled:$('notifications').checked}).then(show).catch(e=>show(String(e))));
bind('check-update',async b=>{$('update-consent').checked=false;const value=await run(b,'check-update');$('update-info').textContent=value.available?'可用更新：'+value.version+'\n'+(value.body||''):'已是当前渠道最新版本';$('install-update').disabled=!value.available;});
bind('install-update',b=>{if(!$('update-consent').checked){show('请先确认更新与备份。');return;}return run(b,'install-update',{confirmed:true});});
for(const [button,input]of[['choose-backup','backup'],['choose-restore','restore-home']])bind(button,async b=>{const path=await run(b,'choose-directory');if(path)$(input).value=path;});
bind('restore',b=>{if(!$('restore-consent').checked){show('请先确认备份后修改不会保留。');return;}return run(b,'restore',{home:$('restore-home').value.trim(),backup_directory:$('backup').value.trim(),confirmed:true});});
window.__TAURI__.event.listen('startup-error',e=>show(e.payload));
window.__TAURI__.event.listen('menu-action',e=>{if(e.payload==='diagnostics')$('diagnose').click();if(e.payload==='update')$('check-update').focus();});
invoke('state').then(state=>{$('version').textContent=state.version;$('home').value=state.preferences.home||'';$('notifications').checked=state.preferences.notifications;if(state.error)show(state.error);}).catch(e=>show(String(e)));
