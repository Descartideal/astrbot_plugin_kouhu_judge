window.__mathError = null;
try {
  renderMathInElement(document.querySelector('main'), {
    delimiters: [{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false},{left:'$',right:'$',display:false}],
    throwOnError:true, strict:'error', trust:false,
    errorCallback:(message,error)=>{window.__mathError=String(message)+': '+String(error);},
    ignoredTags:['script','noscript','style','textarea','pre','code']
  });
} catch(e) { window.__mathError=String(e); }
window.__mathReady=true;
