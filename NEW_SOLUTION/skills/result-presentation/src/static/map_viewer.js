const PAL=["#1e7d46","#c25a33","#2662a6","#9b6b16","#7a4fb2","#0f766e","#b42359","#5f7f24","#bd6b00","#3b5b92"];
let manifest=null,drawn=[],renderToken=0,currentAnalysis=null;
const $=id=>document.getElementById(id),fmt=n=>new Intl.NumberFormat("pl-PL").format(n||0);
const map=L.map("map",{preferCanvas:true}),parcelLayer=L.layerGroup().addTo(map);
const baseLayers={
  street:L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:20,attribution:"© OpenStreetMap"}),
  satellite:L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",{maxZoom:20,attribution:"Tiles © Esri"})
};
let activeBase=baseLayers.street.addTo(map);
L.control.layers({"Ulice":baseLayers.street,"Satelita":baseLayers.satellite},{},{position:"bottomright"}).addTo(map);

function showError(message){$("err").textContent=message;$("err").style.display="block"}
function clearError(){$("err").style.display="none"}
function boundsFromBbox(bbox){return L.latLngBounds([bbox[0],bbox[1]],[bbox[2],bbox[3]])}
function currentBbox(){const b=map.getBounds(),sw=b.getSouthWest(),ne=b.getNorthEast();return [sw.lat,sw.lng,ne.lat,ne.lng].join(",")}
function areaText(value){if(!Number.isFinite(value))return"∞";if(value>=10000)return`${(value/10000).toLocaleString("pl-PL",{maximumFractionDigits:2})} ha`;return`${Math.round(value).toLocaleString("pl-PL")} m²`}
function parcelArea(value){return value?areaText(value):"brak area_m2"}
function hashText(text){let h=2166136261;for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}
function colorKey(parcel){return parcel[$("color").value]||"unknown"}
function colorFor(parcel){return PAL[hashText(colorKey(parcel))%PAL.length]}
function maxLimit(){return Number(manifest?.max_limit||8000)}
function objectLimit(){const raw=parseInt($("limit").value||manifest?.default_limit||500,10);return Math.max(5,Math.min(maxLimit(),raw))}
function maxArea(){return Math.max(1,Number(manifest?.area_max_m2||1))}
function sliderArea(raw){const p=Math.max(0,Math.min(1000,parseInt(raw||"0",10)));return Math.max(0,Math.round(Math.pow(10,(p/1000)*Math.log10(maxArea()+1))-1))}
function normalizeArea(changed){let a=parseInt($("areaMin").value||"0",10),b=parseInt($("areaMax").value||"1000",10);if(a>b){if(changed==="min")b=a;else a=b}$("areaMin").value=String(a);$("areaMax").value=String(b);return[a,b]}
function areaFilter(changed){const [a,b]=normalizeArea(changed),filter={min:sliderArea(a),max:sliderArea(b)};$("areaMinOut").textContent=areaText(filter.min);$("areaMaxOut").textContent=areaText(filter.max);return filter}

function setBaseLayer(name){const layer=baseLayers[name]||baseLayers.street;if(activeBase!==layer){map.removeLayer(activeBase);activeBase=layer.addTo(map)}$("base").value=name;redraw()}
function drawParcel(parcel){
  if(!parcel.geometry?.length)return false;
  const color=colorFor(parcel),fillOpacity=$("base").value==="satellite"?.34:.24;
  for(const rings of parcel.geometry){
    L.polygon(rings,{color,weight:2,opacity:.98,fillColor:color,fillOpacity}).bindPopup(
      `<div class="popup"><b>${parcel.number||parcel.id}</b>${parcel.commune||""} / ${parcel.precinct||"brak obrębu"}<br>${parcelArea(parcel.area_m2)}<br>Kolor: ${colorKey(parcel)}<br><small>${parcel.id}</small></div>`
    ).addTo(parcelLayer);
  }
  return true;
}
function redraw(){parcelLayer.clearLayers();let n=0;for(const parcel of drawn)if(drawParcel(parcel))n++;$("drawn").textContent=fmt(n)}
function debounce(fn,delay=140){let timer=0;return(...args)=>{clearTimeout(timer);timer=setTimeout(()=>fn(...args),delay)}}
const debouncedRender=debounce(()=>render(),120);

function analysisMode(){return $("viewMode").value==="analysis"}
function activeAnalysisKey(){return analysisMode()&&currentAnalysis?currentAnalysis.key:""}
function setAnalysisPanel(analysis,show=true){
  if(!analysis){$("analysisPanel").style.display="none";return}
  $("analysisTitle").textContent=analysis.title||"Analiza";
  $("analysisMeta").textContent=`${fmt(analysis.count||0)} działek · ${analysis.source||""}`;
  $("analysisDescription").textContent=analysis.description||"Brak opisu analizy.";
  $("analysisPanel").style.display=show?"block":($("analysisPanel").style.display||"none");
  $("analysisPanel").classList.remove("collapsed");
  $("collapseAnalysis").textContent="zwiń";
}
async function loadAnalysisFromServer(path){
  if(!path)return;clearError();
  const response=await fetch(`/api/analysis?file=${encodeURIComponent(path)}`,{cache:"no-store"});
  if(!response.ok)throw new Error(`analysis HTTP ${response.status}`);
  currentAnalysis=await response.json();
  $("viewMode").value="analysis";
  setAnalysisPanel(currentAnalysis,true);
  await render();
}
async function uploadAnalysisFile(file){
  if(!file)return;clearError();
  const text=await file.text();
  const response=await fetch(`/api/analysis/upload?name=${encodeURIComponent(file.name)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:text});
  if(!response.ok)throw new Error(`upload HTTP ${response.status}`);
  currentAnalysis=await response.json();
  $("viewMode").value="analysis";
  setAnalysisPanel(currentAnalysis,true);
  await render();
}

async function render(){
  if(!manifest)return;
  if(analysisMode()&&!currentAnalysis){
    drawn=[];redraw();$("visible").textContent="0";$("modeLabel").textContent="analiza";
    $("status").textContent="Tryb analizy: wybierz JSON z analysis/ albo wczytaj plik.";
    return;
  }
  const token=++renderToken,limit=objectLimit(),af=areaFilter();
  $("limitOut").textContent=fmt(limit);$("modeLabel").textContent=analysisMode()?"analiza":"ogólny";$("status").textContent="Ładowanie poligonów...";
  try{
    const q=new URLSearchParams({bbox:currentBbox(),limit:String(limit),min_area:String(af.min),max_area:String(af.max)});
    const analysisKey=activeAnalysisKey();if(analysisKey)q.set("analysis_key",analysisKey);
    const response=await fetch(`/api/parcels?${q.toString()}`,{cache:"no-store"});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const payload=await response.json();if(token!==renderToken)return;
    drawn=payload.parcels||[];redraw();$("visible").textContent=fmt(payload.matched);
    const range=`${areaText(af.min)}–${areaText(af.max)}`,analysisLabel=analysisKey?` · analiza: ${currentAnalysis?.title||"bez nazwy"}`:"";
    $("status").textContent=payload.matched>drawn.length
      ?`${fmt(payload.matched)} działek przecina widok i filtr ${range}${analysisLabel}. Rysuję ${fmt(drawn.length)}.`
      :`${fmt(payload.matched)} działek przecina widok i filtr ${range}${analysisLabel}.`;
  }catch(error){showError(`Nie udało się załadować poligonów: ${error.message}`)}
}

function bboxAreaKm2(bbox){const mid=(bbox[0]+bbox[2])/2,h=Math.abs(bbox[2]-bbox[0])*111.32,w=Math.abs(bbox[3]-bbox[1])*111.32*Math.cos(mid*Math.PI/180);return Math.max(0.000001,w*h)}
function populateJumps(){
  const groups=(manifest.groups?.precinct||manifest.groups?.commune||[]).filter(item=>item.count>=2&&item.bbox).map(item=>({item,density:item.count/bboxAreaKm2(item.bbox)})).sort((a,b)=>b.density-a.density).slice(0,20);
  for(const {item,density} of groups){const option=document.createElement("option");option.value=JSON.stringify(item.bbox);option.textContent=`${item.label} · ${fmt(item.count)} dz. · ${density.toFixed(1)}/km²`;$("jump").appendChild(option)}
}
async function loadAnalysisList(){
  const response=await fetch("/api/analysis-files",{cache:"no-store"});if(!response.ok)return;
  const payload=await response.json();
  for(const file of payload.files||[]){const option=document.createElement("option");option.value=file.path;option.textContent=file.path;$("analysisSelect").appendChild(option)}
}

async function init(){
  try{
    const response=await fetch("/api/manifest",{cache:"no-store"});
    if(!response.ok)throw new Error(`manifest HTTP ${response.status}`);
    manifest=await response.json();
    $("total").textContent=fmt(manifest.parcel_count);$("limit").max=String(maxLimit());$("limit").value=manifest.default_limit||500;$("limitOut").textContent=fmt(objectLimit());
    $("areaMin").value="0";$("areaMax").value="1000";areaFilter();populateJumps();await loadAnalysisList();
    if(manifest.bounds)map.fitBounds(boundsFromBbox(manifest.bounds),{padding:[24,24]});else map.setView([52,19],6);
    map.on("moveend zoomend",debounce(render));
    map.on("baselayerchange",event=>{$("base").value=event.layer===baseLayers.satellite?"satellite":"street";redraw()});
    $("limit").addEventListener("input",debounce(render,90));
    $("areaMin").addEventListener("input",()=>{areaFilter("min");debouncedRender()});
    $("areaMax").addEventListener("input",()=>{areaFilter("max");debouncedRender()});
    $("color").addEventListener("change",redraw);
    $("base").addEventListener("change",()=>setBaseLayer($("base").value));
    $("jump").addEventListener("change",()=>{if($("jump").value)map.fitBounds(boundsFromBbox(JSON.parse($("jump").value)),{padding:[48,48]})});
    $("viewMode").addEventListener("change",()=>{setAnalysisPanel(currentAnalysis,analysisMode());render()});
    $("analysisSelect").addEventListener("change",()=>loadAnalysisFromServer($("analysisSelect").value).catch(error=>showError(`Nie udało się wczytać analizy: ${error.message}`)));
    $("analysisFile").addEventListener("change",()=>uploadAnalysisFile($("analysisFile").files[0]).catch(error=>showError(`Nie udało się wczytać pliku: ${error.message}`)));
    $("analysisInfoBtn").addEventListener("click",()=>{if(currentAnalysis)setAnalysisPanel(currentAnalysis,true)});
    $("collapseAnalysis").addEventListener("click",()=>{$("analysisPanel").classList.toggle("collapsed");$("collapseAnalysis").textContent=$("analysisPanel").classList.contains("collapsed")?"rozwiń":"zwiń"});
    await render();
  }catch(error){showError(`Nie udało się uruchomić mapy: ${error.message}`)}
}
init();
