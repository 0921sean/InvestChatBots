// 옛 Service Worker가 fetch 가로채서 잘못된 응답 줄 수 있음 — 모두 제거
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations()
    .then(rs => { if (rs.length) console.log('[sw] 옛 SW', rs.length, '개 제거'); rs.forEach(r => r.unregister()); })
    .catch(() => {});
}
// window.fetch 가로채기 감지 — native가 아니면 XHR로 직행하라고 플래그
window._fetchHijacked = !window.fetch.toString().includes('[native code]');
if (window._fetchHijacked) console.warn('[diag] window.fetch overridden — XHR fallback 사용');
// 캐시 스토리지도 정리
if ('caches' in window) {
  caches.keys().then(keys => { if (keys.length) console.log('[cache] 옛 캐시', keys.length, '개 제거'); keys.forEach(k => caches.delete(k)); }).catch(() => {});
}

// 출처 표식(?s=…) 정리 — 서버가 이미 첫 진입 시 기록함. 주소창은 깨끗하게.
// 사용자가 이 URL 그대로 복사·재공유하면 출처 식별이 잘못 섞임 방지.
try {
  const u = new URL(window.location.href);
  if (u.searchParams.has('s')) {
    window._visitSource = u.searchParams.get('s');  // 비콘 전송용 — URL 정리 전에 먼저 캡처
    u.searchParams.delete('s');
    const clean = u.pathname + (u.searchParams.toString() ? '?' + u.searchParams.toString() : '') + u.hash;
    window.history.replaceState({}, '', clean);
  }
} catch (e) { /* old browser */ }

const BOT_COLORS = {
  // 메인(장기) 데스크
  드가자:'#ef4444', INTJ:'#3b82f6', 퀀트중독자:'#8b5cf6',
  빅픽처:'#f59e0b', 역추세봇:'#f59e0b', 차트천재:'#10b981',
  기본농부:'#06b6d4', 실적왕:'#22d3ee',
  // 트레이딩 데스크
  추세질주:'#fb7185', 테마사냥꾼:'#f472b6', 세력추적:'#c084fc', 바닥픽:'#38bdf8', 칼손절:'#94a3b8',
  // 구 이름·비활성 (과거 메시지 호환)
  경력직:'#06b6d4', 원칙주의자:'#f97316', 감독:'#a855f7', 류:'#f43f5e', 알렉스:'#0ea5e9',
  User:'#fbbf24', System:'#6e7681',
  // AI펀드 봇(전략 노출 방지 — 공개 표기는 ROT13). 채팅 피드·Members 로스터 색 일치.
  // 내부 A/P/W/H/S/Q/M/R → 공개 N/C/J/U/F/D/Z/E. (구 표기는 과거 메시지 호환용으로 남겨둠)
  N:'#8b949e', C:'#5aa9e6', J:'#c9a227', U:'#a78bfa', F:'#e08a3c', D:'#4bbf8a', Z:'#8b8cf0', E:'#e0637c', T:'#ffd166',
  A:'#8b949e', P:'#5aa9e6', W:'#c9a227', H:'#a78bfa', S:'#e08a3c', Q:'#4bbf8a'
};
let lastId = 0, agents = {};

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}
function highlight(s) {
  return esc(s)
    .replace(/\[결정\]\s*매수/g,'<span class="decision-buy">[결정] 매수</span>')
    .replace(/\[결정\]\s*매도/g,'<span class="decision-sell">[결정] 매도</span>')
    .replace(/\[결정\]\s*관망/g,'<span class="decision-hold">[결정] 관망</span>');
}
function timeStr(ts) {
  if (!ts) return '';
  const d = new Date(ts.includes('T') ? ts : ts.replace(' ','T')+'Z');
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  return isToday
    ? d.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})
    : d.toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
function avatar(name, color) {
  return `<div class="avatar" style="color:${color};background:${color}18;border-color:${color}55">${name[0]}</div>`;
}

const _botDisplay = n => n;   // fund 봇 닉네임 = 단일 알파벳(A·P·W·H·S·Q) — 코드·내레이션·UI 통일

function renderMessage(msg) {
  const el = document.createElement('div');
  const color = BOT_COLORS[msg.agent_name] || '#8b949e';
  const disp = _botDisplay(msg.agent_name);
  // 후원 라운드 메시지인지 (round_topic이 '후원'으로 시작)
  const isDonation = (msg.round_topic || '').startsWith('후원');

  if (msg.agent_name === 'System') {
    el.className = 'msg-system' + (isDonation ? ' msg-donation-system' : '');
    el.innerHTML = `<span>${esc(msg.content)}</span>`;
  } else if (msg.agent_name === 'User') {
    el.className = 'msg user-msg';
    el.innerHTML = `${avatar('나',color)}<div class="msg-body"><div class="msg-meta"><span class="msg-name" style="color:${color}">나</span><span>${timeStr(msg.timestamp)}</span></div><span class="msg-bubble">${esc(msg.content)}</span></div>`;
  } else {
    const sub = agents[msg.agent_name]?.description || '';
    el.className = 'msg' + (isDonation ? ' msg-donation' : '');
    const donationTag = isDonation ? '<span style="font-size:9px;background:rgba(63,185,80,.18);color:#3fb950;padding:1px 6px;border-radius:6px;font-weight:700;margin-left:4px">💚 후원 인사</span>' : '';
    el.innerHTML = `${avatar(disp,color)}<div class="msg-body"><div class="msg-meta"><span class="msg-name" style="color:${color}">${esc(disp)}</span><span style="color:#6e7681;font-size:10px">${esc(sub)}</span>${donationTag}<span>${timeStr(msg.timestamp)}</span></div><span class="msg-bubble">${highlight(msg.content)}</span></div>`;
  }
  return el;
}

const MAX_DOM_MESSAGES = 300;  // DOM에 유지할 최대 메시지 수

function scrollToBottom() {
  const c = document.getElementById('messages');
  if (!c) return;
  setTimeout(() => { c.scrollTop = c.scrollHeight + 9999; }, 100);
}

function trimMessages() {
  const c = document.getElementById('messages');
  // children.length - 1 은 앵커 엘리먼트
  while (c.children.length - 1 > MAX_DOM_MESSAGES) {
    const removed = c.firstChild;
    if (removed && removed.dataset && removed.dataset.mid) {
      _renderedMsgIds.delete(parseInt(removed.dataset.mid));
    }
    c.removeChild(removed);
  }
}

let _initialLoaded = false;

async function initialLoad() {
  try {
    const res = await fetch('/api/messages/latest?n=200' + (window.__FUND_MODE__ ? '&desk=fund' : ''));
    if (!res.ok) return;
    const msgs = await res.json();
    const c = document.getElementById('messages');
    const anchor = document.getElementById('msg-anchor');
    msgs.forEach(m => {
      _renderedMsgIds.add(m.id);
      const el = renderMessage(m);
      el.dataset.mid = m.id;
      c.insertBefore(el, anchor);
      if (m.id > lastId) lastId = m.id;
    });
    _initialLoaded = true;
    scrollToBottom();
  } catch(e) { _initialLoaded = true; }
}

// 폴링 재진입 방지 + 이미 그린 메시지 ID 추적
let _polling = false;
const _renderedMsgIds = new Set();
async function poll() {
  if (!_initialLoaded || _polling || document.hidden) return;   // 백그라운드 탭은 폴링 중지(#233)
  _polling = true;
  try {
    const res = await fetch(`/api/messages?since=${lastId}` + (window.__FUND_MODE__ ? '&desk=fund' : ''));
    if (!res.ok) return;
    const msgs = await res.json();
    if (!msgs.length) return;
    const c = document.getElementById('messages');
    const atBottom = c.scrollHeight - c.scrollTop - c.clientHeight < 120;
    const anchor = document.getElementById('msg-anchor');
    msgs.forEach(m => {
      // 이미 그려진 메시지면 skip (재진입·중복 fetch 대비 이중 안전)
      if (_renderedMsgIds.has(m.id)) return;
      _renderedMsgIds.add(m.id);
      const el = renderMessage(m);
      el.dataset.mid = m.id;
      c.insertBefore(el, anchor);
      if (m.id > lastId) lastId = m.id;
    });
    if (atBottom) scrollToBottom();
  } catch(e) {} finally {
    _polling = false;
  }
}

function openBotModal(a) {
  if (!isOwner) return;  // visitor에겐 봇 성격 모달 비공개
  const color = BOT_COLORS[a.name] || '#8b949e';
  document.getElementById('bm-dot').style.background = color;
  document.getElementById('bm-name').textContent = a.name;
  document.getElementById('bm-name').style.color = color;
  document.getElementById('bm-model').textContent = (a.model_label || a.model_provider) + ' · ' + a.model_provider;
  document.getElementById('bm-desc').textContent = a.description;
  // 시스템 프롬프트 파싱 — 말투 예시/기준 강조
  const sys = (a.system || '').trim();
  const el = document.getElementById('bm-system');
  el.innerHTML = sys.split('\n').map(line => {
    line = esc(line);
    if (line.startsWith('예시)') || line.startsWith('- "') || line.startsWith('"')) {
      return `<span style="color:#8b949e;font-style:italic">${line}</span>`;
    }
    if (line.match(/^(투자 (기준|철학)|말투[:\s])/)) {
      return `<span style="color:#e3b341;font-weight:600">${line}</span>`;
    }
    if (line.match(/^\s*-\s/) || line.match(/^[0-9]+\./)) {
      return `<span style="color:#c9d1d9">${line}</span>`;
    }
    return line;
  }).join('\n');
  document.getElementById('bot-modal').classList.add('open');
}
function closeBotModal() {
  document.getElementById('bot-modal').classList.remove('open');
}

async function loadAgents() {
  try {
    const res = await fetch('/api/agents');
    const list = await res.json();
    window._agentsData = list;
    list.forEach(a => { agents[a.name] = a; });  // 매수근거 매핑 등엔 전체 봇 유지
    _renderMembers(window._activeDesk || 'main');
  } catch(e) {}
}

function _renderMembers(desk) {
  if (window.__FUND_MODE__) return;   // fund 모드는 renderFundMembers가 담당
  const list = window._agentsData || [];
  const el = document.getElementById('member-list');
  if (!el) return;
  el.innerHTML = '';
  list.filter(a => !a.hidden_from_members && (a.desk||'main') === desk).forEach(a => {
    const color = BOT_COLORS[a.name] || a.color || '#8b949e';
    const row = document.createElement('div');
    row.className = 'member';
    row.title = isOwner ? '클릭하면 투자 스타일 확인' : '';
    row.onclick = () => openBotModal(a);
    row.innerHTML = `<span class="dot" style="background:${color}"></span><span class="member-name" style="color:${color}">${a.name}</span>`;
    el.appendChild(row);
    const sub = document.createElement('div');
    sub.className = 'member-sub';
    sub.innerHTML = `${esc(a.description)}<br><span style="color:#444c56;font-size:9px">${esc(a.model_label||a.model_provider)}</span>`;
    el.appendChild(sub);
  });
  // '나' 행은 장기 데스크에만 (감독 페르소나 = 장기 비전 담당)
  if (desk === 'main') {
    const youRow = document.createElement('div');
    youRow.className = 'member';
    youRow.style.marginTop = '4px';
    youRow.style.borderTop = '1px solid #21262d';
    youRow.style.paddingTop = '6px';
    youRow.innerHTML = `<span class="dot" style="background:#fbbf24"></span><span class="member-name" style="color:#fbbf24">나</span>`;
    el.appendChild(youRow);
  }
}

async function loadMarket(pre) {
  try {
    const snap = pre ?? await (await fetch('/api/market')).json();
    if (snap && snap.data) {
      const d = JSON.parse(snap.data);
      const md = d.data || {};
      const showKeys = ['NASDAQ', 'S&P500', 'KOSPI', 'KOSDAQ'];
      const lines = showKeys.map(k => {
        const v = md[k];
        if (!v || v.price==null) return '';
        const arrow = (v.change_pct||0)>=0 ? '▲' : '▼';
        const col   = (v.change_pct||0)>=0 ? '#f85149' : '#58a6ff';  /* 상승 빨강 / 하락 파랑 (한국식) */
        return `<span style="color:${col}">${k} ${arrow}${Math.abs(v.change_pct||0).toFixed(1)}%</span>`;
      }).filter(Boolean);
      document.getElementById('market-ticker').innerHTML = `<span class="live">● Live</span><br>${lines.join(' &nbsp; ')}`;
    }
  } catch(e) {}
}

let _sectors = [];
async function loadState(pre) {
  if (window.__FUND_MODE__) return;   // fund 모드는 committee 사이클 상태(섹터·헤더) 미사용
  try {
    if (!_sectors.length) {
      const sr = await fetch('/api/sectors');
      _sectors = await sr.json();
    }
    const s = pre ?? await (await fetch('/api/state')).json();
    window._lastState = s;  // 일정표 모달이 현재 상태 참조

    document.getElementById('sector-name-text').textContent = s.sector || '—';
    // 섹터 N/총개수 — cycle_rest면 완료 표시
    const total = s.total_sectors || _sectors.length || 13;
    const idx = (s.sector_idx || 0);
    if (s.phase === 'cycle_rest') {
      document.getElementById('sector-count').textContent = `${total}/${total} ✓`;
    } else {
      document.getElementById('sector-count').textContent = `${idx + 1}/${total}`;
    }
    let phaseText = '';
    if (s.phase === 'cycle_rest') {
      phaseText = `오늘 분석 완료 😴 ${s.cooldown_remaining_h||0}h 후 재시작`;
    } else if (s.phase === 'sector_discussion') {
      phaseText = `섹터 토론 ${s.discussion_round||0}/3 라운드`;
    } else {
      phaseText = `종목 분석 ${(s.stock_idx||0)+1}/${s.stocks_total||3} — ${s.stock||''}`;
    }
    document.getElementById('sector-phase').textContent = phaseText;

    // 채팅 헤더 해시태그 — activity 우선, 없으면 phase 기반
    const hashEl = document.getElementById('chat-hash');
    const subEl = document.getElementById('round-label');
    if (hashEl && subEl) {
      if (s.activity === 'watchlist') {
        hashEl.textContent = '# 서브 사이클';
        subEl.textContent = '텔레그램·뉴스 신규 종목 스캔 중';
      } else if (s.activity === 'holdings_review') {
        hashEl.textContent = '# 종목 점검';
        subEl.textContent = '보유 종목 홀드/매도 판단 중';
      } else if (s.phase === 'sector_discussion') {
        hashEl.textContent = '# 메인 사이클';
        subEl.textContent = `${s.sector || ''} · 섹터 토론 ${s.discussion_round||0}/3`;
      } else if (s.phase === 'stock_analysis') {
        hashEl.textContent = '# 메인 사이클';
        subEl.textContent = `${s.sector || ''} · ${s.stock || ''}`;
      } else if (s.phase === 'cycle_rest') {
        hashEl.textContent = '# 휴식';
        subEl.textContent = `오늘 분석 완료 · 다음 사이클 ${s.cooldown_remaining_h||0}h 후`;
      } else {
        hashEl.textContent = '# 채팅';
        subEl.textContent = 'AI 투자 토론방';
      }
    }

    // visitor 모드 카운트다운 즉시 반영
    updateVisitorTimers();

    const rawPct = s.phase === 'stock_analysis'
      ? Math.round(((s.stock_idx||0)/(s.stocks_total||3))*100)
      : Math.round(((s.discussion_round||0)/3)*100);
    const pct = Math.min(100, Math.max(0, rawPct));
    document.getElementById('sector-progress-fill').style.width = pct+'%';

    // 현재 섹터는 이름으로 매칭 (sector_idx는 시장별 부분집합 기준이라 전체 리스트 인덱스와 다름)
    const sec = _sectors.find(x => x.name === s.sector) || _sectors[s.sector_idx||0];
    if (sec) {
      document.getElementById('sector-stocks').innerHTML = sec.stocks.map((name,i) => {
        let cls = 'stock-badge';
        if (s.phase==='stock_analysis') {
          if (i < (s.stock_idx||0)) cls += ' done';
          else if (i === (s.stock_idx||0)) cls += ' current';
        }
        return `<span class="${cls}">${name}</span>`;
      }).join('');
    }
    document.getElementById('round-label').textContent =
      `${s.sector} · ${s.phase==='sector_discussion' ? '섹터 토론' : '종목 분석'}`;
  } catch(e) {}
}

let isPlaying = false;
async function togglePlay() {
  await fetch(isPlaying ? '/api/conversation/stop' : '/api/conversation/start', {method:'POST'});
  await syncPlayStatus();
}
async function syncPlayStatus(pre) {
  try {
    const state = pre ?? await (await fetch('/api/state')).json();   // running 포함
    const running = !!state.running;
    const actuallyWorking = running && state.phase !== 'cycle_rest';
    if (running === isPlaying) {
      // cycle_rest 상태면 버튼 텍스트만 업데이트
      const pauseBtn = document.getElementById('btn-pause');
      if (running && state.phase === 'cycle_rest') {
        pauseBtn.textContent = `😴 휴식 중 (${state.cooldown_remaining_h}h 남음)`;
      } else if (running) {
        pauseBtn.textContent = '⏸ 일시정지';
      }
      document.getElementById('typing').style.display = actuallyWorking ? 'flex' : 'none';
      return;
    }
    isPlaying = running;
    document.getElementById('btn-play').style.display  = running ? 'none' : 'inline-block';
    document.getElementById('btn-pause').style.display = running ? 'inline-block' : 'none';
    document.getElementById('typing').style.display = actuallyWorking ? 'flex' : 'none';
  } catch(e) {}
}

async function loadConsensus(pre) {
  if (window.__FUND_MODE__) return;   // fund 모드는 섹터 합의 패널 미사용
  try {
    const notes = pre ?? await (await fetch('/api/consensus')).json();
    const list = document.getElementById('memo-list');
    const empty = document.getElementById('memo-empty');
    if (!notes.length) { if(empty) empty.style.display='block'; return; }
    if(empty) empty.style.display='none';

    const outlookColor = { '긍정':'#3fb950', '중립':'#adb6c0', '부정':'#f85149' };
    const decisionColor = { '매수':'#79c0ff', '관망':'#e3b341', '매도':'#f85149' };

    // 인라인 스타일로 통일된 카드 — 잘림 표시 제거, 전망/핵심/봇 한마디 형식
    list.innerHTML = notes.map(n => {
      const t = n.created_at
        ? new Date(n.created_at+'Z').toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})
        : '';
      const outlook = n.outlook || '중립';
      const decision = n.decision || '관망';
      const oCol = outlookColor[outlook] || '#adb6c0';
      const dCol = decisionColor[decision] || '#e3b341';
      const thesis = n.thesis || '';
      const picks = Array.isArray(n.key_picks) ? n.key_picks : [];
      const borderStyle = (n.v === 2) ? 'solid' : 'dashed';
      const spokesperson = (n.v === 2) ? (n.spokesperson || '') : '';
      const quote = (n.v === 2) ? (n.quote || '') : '';
      const spColor = (BOT_COLORS && BOT_COLORS[spokesperson]) || '#fbbf24';

      const picksHtml = picks.length
        ? `<div style="margin-top:8px"><span style="font-size:10px;color:#6e7681;font-weight:700">주목 종목</span> ${picks.map(p=>`<span style="font-size:10px;padding:2px 7px;border-radius:8px;background:#21262d;color:#c9d1d9;border:1px solid #30363d;margin-left:4px">${esc(p)}</span>`).join('')}</div>`
        : '';
      const quoteHtml = (spokesperson && quote)
        ? `<div style="margin-top:9px;padding:7px 10px;background:#161b22;border-left:3px solid ${spColor};border-radius:0 6px 6px 0">
             <div style="font-size:10px;font-weight:700;color:${spColor};margin-bottom:2px">${esc(spokesperson)} 한마디</div>
             <div style="font-size:11px;color:#c9d1d9;line-height:1.55;font-style:italic">"${esc(quote)}"</div>
           </div>`
        : '';

      return `
<div style="background:#0d1117;border:1px solid #21262d;border-style:${borderStyle};border-radius:8px;padding:10px 12px">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;padding-bottom:7px;border-bottom:1px solid #21262d">
    <div style="font-size:12.5px;font-weight:700;color:#e2e8f0;min-width:0;flex:1;line-height:1.3">${esc(n.sector||'—')}<div style="font-size:9.5px;color:#6e7681;font-weight:400;margin-top:2px">${t}</div></div>
    <div style="display:flex;gap:4px;flex-shrink:0;flex-direction:column;align-items:flex-end">
      <span style="font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:8px;background:${oCol}26;color:${oCol}">방향: ${outlook}</span>
      <span style="font-size:9.5px;font-weight:700;padding:2px 6px;border-radius:8px;background:${dCol}26;color:${dCol}">결론: ${decision}</span>
    </div>
  </div>
  <div style="margin-top:8px">
    <span style="font-size:10px;font-weight:700;color:#6e7681">핵심 이유</span>
    <div style="margin-top:4px;font-size:11.5px;line-height:1.65;color:#c9d1d9;white-space:pre-wrap">${esc(thesis || '합의 본문 없음')}</div>
  </div>
  ${quoteHtml}
  ${picksHtml}
</div>`;
    }).join('');
  } catch(e) { console.error('loadConsensus error:', e); }
}

function _openPosItemHtml(p) {
  const isUS = p.market === 'US';
  const fmt = (v, us) => us
    ? `$${Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`
    : `₩${Math.round(v).toLocaleString()}`;
  const entryStr   = fmt(p.entry_price||0, isUS);
  const currentStr = p.current_price ? fmt(p.current_price, isUS) : '—';
  // 손익 0(보합)과 손익 미산출(시세 없음)은 다르다 — 옛 조건 `upnl !== 0`은 둘을 같이 묶어
  // 정확히 보합인 종목의 손익 배지를 통째로 지웠다. 다른 종목엔 다 붙어 있으니 "안 뜬다"로 보인다.
  const upnl  = p.unrealized_pnl;
  const upnlP = p.unrealized_pnl_pct;
  const hasPnl = typeof upnl === 'number' && typeof upnlP === 'number';
  const upnlColor = upnl > 0 ? '#f85149' : upnl < 0 ? '#58a6ff' : '#8b949e';   // 보합 = 중립 회색
  const upnlStr = hasPnl
    ? `<span style="color:${upnlColor};font-size:10px">${upnl>0?'+':''}₩${Math.round(Math.abs(upnl)).toLocaleString()} (${upnlP>0?'+':''}${upnlP.toFixed(1)}%)</span>`
    : '';
  const posIdx = window._posData.length;
  window._posData.push(p);
  return `<div class="pos-item" onclick="showPositionReason(${posIdx})">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-weight:600;color:#e2e8f0">${p.symbol}<span style="color:#3fb950;margin-left:4px;font-size:9px">${isUS?'US':'KR'}</span></span>
        ${upnlStr}
      </div>
      <div class="pos-meta">진입 ${entryStr} → 현재 <span style="color:#e2e8f0">${currentStr}</span></div>
      <div class="pos-meta">투자금 ₩${Math.round(p.amount||0).toLocaleString()} <span style="color:#444c56">· 클릭해서 매수 이유 보기</span></div>
    </div>`;
}
function _closedPosItemHtml(p) {
  const win = (p.pnl||0)>=0;
  const posIdx = window._posData.length;
  window._posData.push(p);
  return `<div class="pos-item" onclick="showPositionReason(${posIdx})">
      <div><span style="font-weight:600;color:#e2e8f0">${p.symbol}</span>
        <span style="color:${win?'#f85149':'#58a6ff'};font-size:10px;margin-left:6px">${win?'+':''}${(p.pnl_pct||0).toFixed(1)}%</span></div>
      <div class="pos-meta">${win?'이익':'손실'} ₩${Math.round(Math.abs(p.pnl||0)).toLocaleString()} <span style="color:#444c56">· 클릭해서 매수/매도 이유 보기</span></div>
    </div>`;
}

async function loadPortfolio(pre) {
  if (window.__FUND_MODE__) return;   // fund 모드는 renderFundAccounts가 오른쪽 패널 담당
  try {
    const pf = pre ?? await (await fetch('/api/portfolio')).json();
    // 두 계좌 데이터 캐시 (전환 시 재요청 없이 즉시 렌더)
    window._pfData = {
      main: { balance: pf.balance, invested: pf.invested, total_pnl: pf.total_pnl,
              win_count: pf.win_count, loss_count: pf.loss_count,
              initial_balance: pf.initial_balance || 100000000, positions: pf.positions || [] },
      sub:  pf.sub || { balance: 0, invested: 0, total_pnl: 0, win_count: 0, loss_count: 0,
                        initial_balance: 25000000, positions: [] },
    };
    // 합산(combined) = 장기+트레이딩 실데이터 합
    const _m = window._pfData.main, _s = window._pfData.sub;
    window._pfData.combined = {
      balance: _m.balance + _s.balance, invested: _m.invested + _s.invested,
      total_pnl: _m.total_pnl + _s.total_pnl,
      win_count: _m.win_count + _s.win_count, loss_count: _m.loss_count + _s.loss_count,
      initial_balance: _m.initial_balance + _s.initial_balance,
      positions: [...(_m.positions||[]), ...(_s.positions||[])],
    };
    const ms = pf.market_status;
    document.getElementById('market-status-label').textContent =
      ms==='KRX' ? '🟢 국장 장중' : ms==='US' ? '🔵 미장 장중' : '⚫ 장외 시간';
    _renderPfAccount(_acctForDesk(window._activeDesk||'main'));
  } catch(e) { console.error('pf',e); }
}

// 포트폴리오·Members 공유 데스크 상태 — 하나 전환하면 둘 다 전환
// 시간 기반 기본 탭 — 트레이딩 사이클 동안에만 트레이딩 우선, 그 외 전부 장기. '로드 시점'에만 결정.
// 트레이딩 사이클(서브) 시각 = 개장 직후: 국장 09:05 · 미장 ~22:30 (KST, 서머타임 근사).
const TRADING_CYCLE_HOURS = [9, 22.5];
const TRADING_WINDOW_HOURS = 2;   // 정각 ~ +2h 동안만 트레이딩 우선
function _defaultDeskForNow(now) {
  now = now || _kstNow();
  const hf = now.getUTCHours() + now.getUTCMinutes() / 60;  // _kstNow 보정값 → KST 시
  for (const h of TRADING_CYCLE_HOURS) {
    const end = h + TRADING_WINDOW_HOURS;
    const inWin = (end <= 24) ? (hf >= h && hf < end) : (hf >= h || hf < end - 24);
    if (inWin) return 'trading';   // [정각, 정각+2h)
  }
  return 'main';   // 트레이딩 창 밖은 전부 장기 우선
}
window._activeDesk = window._activeDesk || _defaultDeskForNow();  // 'main'(장기) | 'trading'(트레이딩)
async function loadBenchmark(pre) {
  const el = document.getElementById('pf-benchmark');
  if (!el) return;
  try {
    const b = pre ?? await (await fetch('/api/benchmark')).json();   // 지수는 시장 공통(committee·데스크 동일)
    if (!b.available) {
      el.innerHTML = '<div class="pf-bm-title">봇 vs 지수</div><div class="pf-bm-empty">데이터 없음 — 스냅샷 누적 중(첫 가동)</div>';
      return;
    }
    const fmt = v => v == null
      ? '<span class="pf-bm-na">데이터 없음</span>'
      : `<span class="${v >= 0 ? 'pf-pos' : 'pf-neg'}">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
    const row = (label, v, strong) => `<div class="pf-bm-row${strong ? ' strong' : ''}"><span>${label}</span>${fmt(v)}</div>`;
    // fund 모드: 봇 = 데스크(아직 NAV 없어 데이터없음), 지수는 공통
    const botLabel = window.__FUND_MODE__ ? '🤖 봇(데스크)' : '🤖 봇(합산)';
    const botVal = window.__FUND_MODE__ ? null : b.bot;
    el.innerHTML = `<div class="pf-bm-title">봇 vs 지수 <span class="pf-bm-sub">${b.baseline_date}~ 누적 (${b.days}일)</span></div>`
      + row(botLabel, botVal, true)
      + row('QQQ (나스닥100)', b.qqq)
      + row('SPY (S&P500)', b.spy)
      + row('코스피', b.kospi)
      + row('코스닥', b.kosdaq);
  } catch (e) { console.error('benchmark', e); }
}

function _acctForDesk(desk) { return desk === 'trading' ? 'sub' : desk === 'all' ? 'combined' : 'main'; }
function setDesk(desk) {
  window._activeDesk = desk;
  const toggle = (id, on) => { const e = document.getElementById(id); if (e) e.classList.toggle('active', on); };
  toggle('pf-tab-main', desk === 'main'); toggle('pf-tab-sub', desk === 'trading'); toggle('pf-tab-all', desk === 'all');
  toggle('mem-tab-main', desk !== 'trading'); toggle('mem-tab-trading', desk === 'trading');
  if (window._pfData) _renderPfAccount(_acctForDesk(desk));
  if (window._agentsData) _renderMembers(desk === 'trading' ? 'trading' : 'main');
}
setDesk(window._activeDesk);  // 로드 시 기본 탭으로 버튼·뷰 동기화 (이후 수동선택·자동변경 없음)

function _renderPfAccount(acct) {
  const d = (window._pfData || {})[acct];
  if (!d) return;
  window._posData = [];  // 모달 참조 캐시 리셋 (현재 계좌 포지션만)
  const INITIAL = d.initial_balance || (acct==='sub' ? 30000000 : 100000000);
  const balance = d.balance || 0, invested = d.invested || 0, pnl = d.total_pnl || 0;
  const positions = d.positions || [];
  const open   = positions.filter(p => p.status==='open');
  const isRestructure = (p) => /^(전략 정렬|오너 지시)/.test((p.exit_reasoning||'').trim());   // 비운용 청산(#201)
  const closed = positions.filter(p => p.status==='closed' && !isRestructure(p)).slice(0,5);
  const unrealizedPnl = open.reduce((s,p) => s + (p.unrealized_pnl||0), 0);
  const rsPnl = d.restructure_pnl || 0;                       // 재편·오너지시 실현 — 운용 성과에서 분리(#201)
  const totalPnl = pnl + unrealizedPnl - rsPnl;
  const retPct = INITIAL ? totalPnl / INITIAL * 100 : 0;
  const rsN = d.restructure_count || 0, rsW = d.restructure_wins || 0;
  const wins = Math.max(0, (d.win_count||0) - rsW), losses = Math.max(0, (d.loss_count||0) - (rsN - rsW));
  const rate = (wins+losses) > 0 ? Math.round(wins/(wins+losses)*100) : 0;

  const pc = v => v>0 ? 'pf-pos' : v<0 ? 'pf-neg' : 'pf-neu';
  document.getElementById('pf-balance').textContent = `₩${Math.round(balance).toLocaleString()}`;
  document.getElementById('pf-balance').className = `pf-value pf-neu`;  /* 현금 잔액은 손익색 아님 — 중립 */
  document.getElementById('pf-invested').textContent = invested > 0 ? `₩${Math.round(invested).toLocaleString()}` : '없음';
  document.getElementById('pf-pnl').textContent = `${totalPnl>=0?'+':''}₩${Math.round(totalPnl).toLocaleString()}`;
  document.getElementById('pf-pnl').className = `pf-value ${pc(totalPnl)}`;
  document.getElementById('pf-return').textContent = `${retPct>=0?'+':''}${retPct.toFixed(2)}%`;
  document.getElementById('pf-return').className = `pf-value ${pc(retPct)}`;
  document.getElementById('pf-record').textContent = `${wins}승 ${losses}패 (${rate}%)`;

  let html = '';
  if (open.length) {
    html += `<div class="pos-section-label">● 보유 중 (${open.length})</div>`;
    open.forEach(p => { html += _openPosItemHtml(p); });
  } else {
    const empty = acct==='sub' ? '트레이딩 보유 없음' : '보유 종목 없음';
    html += `<div style="padding:12px;color:#6e7681;font-size:11px;text-align:center">${empty}</div>`;
  }
  if (closed.length) {
    html += `<div class="pos-section-label">최근 청산</div>`;
    closed.forEach(p => { html += _closedPosItemHtml(p); });
  }
  document.getElementById('pf-positions').innerHTML = html;
}

const CONSENT_KEY = 'icb_chat_consent_v1';

function agreeConsent() {
  localStorage.setItem(CONSENT_KEY, '1');
  document.getElementById('consent-modal').classList.remove('open');
  sendMessage();   // 입력 텍스트 그대로 — 이제 동의 게이트 통과
}
function cancelConsent() {
  document.getElementById('consent-modal').classList.remove('open');
}

async function sendMessage() {
  const input = document.getElementById('msg-input');
  const text = input.value.trim();
  if (!text) return;
  // 면책 1회 동의 게이트 (안전장치 2/3) — 동의 전엔 전송 보류, 입력 유지
  if (!localStorage.getItem(CONSENT_KEY)) {
    document.getElementById('consent-modal').classList.add('open');
    return;
  }
  input.value = ''; input.disabled = true;
  try {
    const res = await fetch('/api/user-message', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:text})});
    const data = await res.json().catch(() => null);
    if (data && data.closed) {
      alert('🛑 ' + (data.message || '오늘 질문이 마감되었습니다.'));
      input.value = text;  // 마감 — 입력 복원
    } else if (data && data.position > 1) {
      alert(`⏳ 대기 ${data.position}번째로 접수됐어요. 순서가 되면 봇들이 답합니다.`);
    }
    loadChatQueue();
  } catch(e) {}
  input.disabled = false; input.focus();
}

async function loadChatQueue(pre) {
  const box = document.getElementById('chat-queue');
  if (!box) return;
  let d = pre;
  if (!d) try { d = await (await fetch('/api/chat/queue')).json(); } catch(e) { return; }
  box.classList.toggle('closed', !!d.closed);
  const head = d.closed
    ? `<span class="cq-cap">🛑 오늘 질문 마감 ${d.today_count}/${d.cap}</span>`
    : `<span class="cq-cap">💬 오늘 ${d.today_count}/${d.cap}</span><span class="cq-cap">대기 ${d.waiting}건</span>`;
  let list = '';
  if ((d.queue || []).length) {
    list = '<ul class="cq-list">' + d.queue.map(q =>
      `<li class="cq-item"><span class="cq-pos">${q.position}.</span><span class="cq-prev">${esc(q.preview)}</span></li>`
    ).join('') + '</ul>';
  } else if (!d.closed) {
    list = '<div class="cq-empty">대기 중인 질문 없음</div>';
  }
  box.innerHTML = `<div class="cq-head">${head}</div>${list}`;
}

async function runMainCycle() {
  const btn = document.getElementById('btn-main-run');
  btn.disabled = true;
  btn.textContent = '확인 중...';
  try {
    const res = await fetch('/api/main-cycle/run', {method:'POST'});
    const data = await res.json();
    if (data.weekend) {
      alert('🛌 ' + data.message);
      btn.textContent = '▶ 메인 실행';
    } else if (data.already_done) {
      alert('✅ ' + data.message);
      btn.textContent = '▶ 메인 실행';
    } else {
      btn.textContent = '▶ 실행 중';
      alert('🚀 ' + data.message);
    }
  } catch(e) {
    btn.textContent = '▶ 메인 실행';
    alert('오류: ' + e);
  } finally {
    btn.disabled = false;
  }
}

async function runSubCycle() {
  const btn = document.getElementById('btn-sub-run');
  if (!confirm('서브 사이클 즉시 실행할까요?')) return;
  btn.disabled = true;
  btn.textContent = '시작 중...';
  try {
    const res = await fetch('/api/watchlist/run', {method:'POST'});
    if (!res.ok) throw new Error('실행 실패 — 서브 사이클 비활성/이미 진행 중일 수 있음');
    btn.textContent = '▶ 실행 됨';
    setTimeout(() => { btn.textContent = '▶ 서브 실행'; btn.disabled = false; }, 4000);
  } catch (e) {
    alert('오류: ' + e.message);
    btn.textContent = '▶ 서브 실행';
    btn.disabled = false;
  }
}

async function requestSummary() {
  const btn = document.getElementById('btn-summary');
  btn.disabled = true; btn.textContent = '⏳...';
  try {
    await fetch('/api/summary/request', {method:'POST'});
    btn.textContent = '✅ 발송';
    setTimeout(() => { btn.textContent = '📋 요약'; btn.disabled = false; }, 3000);
  } catch(e) { btn.textContent = '📋 요약'; btn.disabled = false; }
}

// 표시용 이니셜 치환(ROT13) — 내부/원본은 A/P/W/H/S/Q/M, 화면엔 N/C/J/U/F/D/Z. 단독 레터만(&·/·단어 인접 제외).
const _PUB_LETTER = {A:'N', P:'C', W:'J', H:'U', S:'F', Q:'D', M:'Z', R:'E'};
function cipherLetters(s) {
  return String(s || '').replace(/(?<![\w&/])([APWHSQMR])(?![\w&/])/g, c => _PUB_LETTER[c] || c);
}
// 저장된 매수 이유(랭크 크레딧용 원본)를 초보 친화 문장으로 렌더. 원본은 그대로 두고 화면만 친절하게.
// ── 매수 이유 구조화(#196) — 긴 원문을 '봇별 의견 카드(문장 불릿) + 시스템 칩 + 접기'로 ──
function sentenceBullets(text) {
  const parts = (text || '').split(/\.\s+/).map((t, i, arr) => {
    t = t.trim();
    if (!t) return '';
    return (i < arr.length - 1 || /[.다요음임함]$/.test(t)) && !/\.$/.test(t) ? t + '.' : t;
  }).filter(Boolean);
  const out = [];
  for (const t of parts) {                      // 너무 짧은 조각(약어 등)은 앞 문장에 붙임
    if (out.length && t.length < 15) out[out.length - 1] += ' ' + t;
    else out.push(t);
  }
  return out;
}
const _ROLE_COLOR = { '성장주':'#3fb950', '가치':'#d2a8ff', '실적':'#f0883e', '병목':'#58a6ff', '타이밍':'#8b949e', '품질':'#ffd166', '블로그':'#8b8cf0' };
function parseStructuredReason(raw) {
  // '오너 승인 매수' 류의 긴 포맷 → {chips, opinions:[{role,text}], folds:[{label,text}]} | null(미지원 포맷)
  let r = (raw || '').trim();
  const chips = [], opinions = [], folds = [];
  const mHead = r.match(/^오너 승인 매수\s*\(([^)]*)\)\s*/);
  if (mHead) {
    chips.push('🙆 사장님 결재 승인');
    r = r.slice(mHead[0].length);
  } else if (/^•\s/.test(r)) {                   // committee(v1) '• 봇: 의견' 포맷
    for (const line of r.split(/\n/)) {
      const m = line.match(/^•\s*([^:]+):\s*(.+)$/);
      if (m) opinions.push({ role: m[1].trim(), text: m[2].trim(), color: '#58a6ff' });
    }
    return opinions.length ? { chips, opinions, folds } : null;
  } else {
    return null;                                 // Q 진입 등 짧은 포맷은 friendlyReason이 처리
  }
  const mRej = r.match(/⚠️\s*(\d+일 전 거절하신 종목[^\n]*)\n\[당시 사유\]([\s\S]*?)\[이번 사유\]\s*/);
  if (mRej) {
    chips.push('⚠️ 재상신(이전 거절 이력)');
    folds.push({ label: '이전 거절 당시 사유 보기', text: mRej[2].trim() });
    r = r.slice(mRej.index + mRej[0].length);
  }
  r = r.replace(/\n?\[티어 업[^\n]*\n?/g, () => { if (!chips.includes('⬆️ 비중 확대 (노드 대표)')) chips.push('⬆️ 비중 확대 (노드 대표)'); return '\n'; });
  r = r.replace(/\n?\[계좌 이관\][^\n]*\n?/g, () => { if (!chips.includes('🔁 대형주 계좌로 이관')) chips.push('🔁 대형주 계좌로 이관'); return '\n'; });
  for (let seg of r.split(/\n?·\s+/)) {
    seg = seg.trim();
    if (!seg) continue;
    let m = seg.match(/^(성장주|가치|실적|병목|타이밍|품질) 담당 의견\s*—\s*([\s\S]+)$/);
    if (m) {
      let text = m[2], obs = '';
      const oIdx = text.search(/\n?이후 \d+회에 걸쳐 재점검/);   // 의견 뒤에 붙는 관찰 서사 분리
      if (oIdx >= 0) { obs = text.slice(oIdx).trim(); text = text.slice(0, oIdx).trim(); }
      opinions.push({ role: m[1] + ' 담당', text, color: _ROLE_COLOR[m[1]] || '#58a6ff' });
      if (obs) { const n = (obs.match(/^이후 (\d+)회/) || [])[1]; chips.push(`👀 재점검 ${n || '수'}회`); folds.push({ label: '관찰 기록 보기', text: obs }); }
      continue;
    }
    if (/^최초 검토 —/.test(seg)) { const px = (seg.match(/가격\s*([\d.,]+)/) || [])[1]; chips.push('🔎 후보 선정' + (px ? ` (당시 ${px})` : '')); continue; }
    m = seg.match(/^이후 (\d+)회에 걸쳐 재점검([\s\S]*)$/);
    if (m) { chips.push(`👀 재점검 ${m[1]}회`); if (m[2].trim().length > 20) folds.push({ label: '관찰 기록 보기', text: seg }); continue; }
    if (/^진입 타이밍 —/.test(seg)) { chips.push('⏱️ 타이밍 점검 통과'); continue; }
    if (/^\[티어 업/.test(seg)) { chips.push('⬆️ 비중 확대 (노드 대표)'); continue; }
    if (/^\[계좌 이관\]/.test(seg)) { chips.push('🔁 대형주 계좌로 이관'); continue; }
    m = seg.match(/^\[딥스터디\]\s*([\s\S]+)$/);
    if (m) { folds.push({ label: '셀프 반박(딥스터디) 보기', text: m[1].trim() }); continue; }
    if (/^펀더 심사 —|^블로그에서 출발/.test(seg) || seg.length > 60) {
      opinions.push({ role: /블로그/.test(seg) ? '블로그 시그널' : '매수 근거', text: seg, color: _ROLE_COLOR['블로그'] });
    }
  }
  return opinions.length ? { chips, opinions, folds } : null;
}
function renderStructuredReason(st, raw) {
  let h = '';
  if (st.chips.length) h += `<div class="rc-chips">` + st.chips.map(c => `<span class="rc-chip">${esc(cipherLetters(c))}</span>`).join('') + `</div>`;
  for (const op of st.opinions) {
    const lis = sentenceBullets(op.text).map(t => `<li>${esc(cipherLetters(t))}</li>`).join('');
    h += `<div class="rc-card" style="border-left-color:${op.color || '#58a6ff'}">
            <div class="rc-role" style="color:${op.color || '#79c0ff'}">${esc(cipherLetters(op.role))}</div><ul>${lis}</ul></div>`;
  }
  for (const f of st.folds) h += `<details><summary>${esc(f.label)}</summary><div class="rd-raw">${esc(cipherLetters(f.text))}</div></details>`;
  if (raw) h += `<details><summary>원문 보기</summary><div class="rd-raw">${esc(cipherLetters(raw))}</div></details>`;
  return h;
}

function friendlyReason(raw) {
  const r = (raw || '').trim();
  if (!r) return '매수 이유 정보 없음';
  if (/^committee\s*승계/.test(r)) {
    return "이전 데스크(committee)에서 '실적왕'과 '역추세봇' 봇이 함께 매수를 승인했던, 성과가 좋았던 종목이에요. 새 대형주 계좌로 그대로 이어받아(승계) 계속 보유 중입니다.";
  }
  let m = r.match(/^Q\s*(추세돌파|되돌림)\s*진입(?:\s*·\s*관심\s*([A-Z,\s]+))?/);
  if (m) {
    const strat = m[1] === '추세돌파'
      ? "주가가 그동안의 고점(저항선)을 힘있게 뚫고 올라서는 '추세 돌파' 신호가 나와서, 퀀트 타이밍 봇 Q가 매수했어요. 상승 흐름 초입에 올라타는 전략이에요."
      : "강하게 오른 뒤 잠깐 눌린(되돌림) 자리에서 다시 오를 힘이 남았다고 보고, 퀀트 타이밍 봇 Q가 매수했어요. 짧게 눌렸을 때 담는 전략이에요.";
    const bots = (m[2] || '').replace(/\s/g, '');
    const credit = bots ? ` (${bots.split(',').filter(Boolean).join('·')} 봇이 미리 관심 종목으로 점찍어둔 곳이에요.)` : '';
    return strat + credit;
  }
  m = r.match(/^발굴주\s*매수\s*\(([^)]*)\)(?:\s*—\s*([A-Z])\s*:\s*([\s\S]+))?/);
  if (m) {
    const bots = (m[1] || '').replace(/\s/g, '').split(',').filter(Boolean).join('·');
    const lead = `발굴 담당 봇들이 새로 찾아낸 종목이에요.${bots ? ` ${bots} 봇이 매수에 동의했어요.` : ''}`;
    const detail = m[3] ? ` ${m[2]} 봇의 판단: ${m[3].trim()}` : '';
    return lead + detail;
  }
  m = r.match(/^오너 승인 매수\s*\([^)]*\)\s*(?:·\s*)*([\s\S]*)$/);
  if (m) {
    const detail = (m[1] || '').replace(/^·+\s*/, '').replace(/\n·/g, '\n· ').trim();
    return "🙆 사장님이 직접 검토하고 승인해서 담은 종목이에요. 봇들이 올린 결재 사유는 이렇습니다:\n\n"
      + (detail || "(상세 사유 없음)");
  }
  return r;   // 알 수 없는 형식은 원본 그대로
}

// 매도(청산) 이유 — 봇 내부 용어를 친절한 한 문장으로.
function friendlyExit(raw) {
  const r = (raw || '').trim();
  if (!r) return '';
  if (/펀더\s*청산|논지\s*훼손|펀더매도/.test(r))
    return "처음 살 때의 투자 이유(논리)가 무너졌다고 판단해서 정리했어요. 억지로 들고 가지 않는다는 원칙이에요.";
  if (/지수.*(청산|반전)|50MA\s*반전/.test(r))
    return "지수 추세가 반대 방향으로 꺾이는 신호가 나와서, 규칙대로 자동 정리했어요.";
  if (/추세.*(익절|손절|청산)/.test(r))
    return "타고 있던 상승 추세가 꺾이는 신호(주요 이동평균선 이탈)가 나와서, 퀀트 타이밍 봇이 정리했어요.";
  if (/되돌림.*(익절|손절|청산)/.test(r))
    return "짧은 반등을 노리고 담았던 자리에서 목표에 닿거나 힘이 빠져서, 퀀트 타이밍 봇이 정리했어요.";
  if (/손절|스탑/.test(r))
    return "정해둔 손절선에 닿아서 손실을 키우지 않으려고 정리했어요.";
  if (/^전략 정렬/.test(r))
    return "종목이 나빠서가 아니라, 포트폴리오를 '병목 노드당 대표 1종목' 체제로 재편하면서 정리한 거예요. 이 손익은 봇 운용 성적과 분리해 집계합니다.";
  if (/^오너 지시/.test(r))
    return "사장님 지시로 매수 자체를 재검토해 취소한 거래예요. 봇 운용 성적과 분리해 집계합니다.";
  return r;
}
function showPositionReason(idx) {
  const p = (window._posData || [])[idx];
  if (!p) return;
  document.getElementById('reason-box').classList.remove('wide');  // 기본은 좁게
  const isUS = p.market === 'US';
  const fmtKst = (s) => s ? new Date(s+'Z').toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
  const fmtPrice = (v) => isUS
    ? `$${Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`
    : `₩${Math.round(v).toLocaleString()}`;

  // 헤더
  document.getElementById('r-symbol').textContent = p.symbol + (isUS ? ' (US)' : '');

  // 메타 — 보유/청산 구분
  const metaLines = [];
  metaLines.push(`매수 ${fmtKst(p.opened_at)} · 진입 ${fmtPrice(p.entry_price||0)}`);
  if (p.status === 'closed') {
    const win = (p.pnl||0) >= 0;
    metaLines.push(`매도 ${fmtKst(p.closed_at)} · 청산 ${fmtPrice(p.exit_price||0)}`);
    metaLines.push(`<span style="color:${win?'#f85149':'#58a6ff'};font-weight:700">${win?'+':''}${(p.pnl_pct||0).toFixed(1)}% (${win?'이익':'손실'} ₩${Math.round(Math.abs(p.pnl||0)).toLocaleString()})</span>`);
  } else {
    const upnlP = p.unrealized_pnl_pct;
    if (typeof upnlP === 'number') {
      const win = upnlP >= 0;
      metaLines.push(`현재가 ${p.current_price ? fmtPrice(p.current_price) : '—'} · <span style="color:${win?'#f85149':'#58a6ff'};font-weight:700">${win?'+':''}${upnlP.toFixed(1)}% 평가</span>`);
    }
  }
  document.getElementById('r-meta').innerHTML = metaLines.join('<br>');

  // 본문 — 주가 차트(#230) + A 종목소개 + 친절한 매수 이유 + (있으면) 매도 이유
  let bodyHtml = '';
  if (p.code) bodyHtml += `<div id="r-chart" style="margin:0 0 14px"><div style="color:#6e7681;font-size:10.5px;padding:6px 0">📈 차트 불러오는 중…</div></div>`;
  const rep = (window._fundReportByCode || {})[p.code];
  const introHtml = (sum) => `<div id="r-intro" style="white-space:normal"><div style="font-size:11px;font-weight:700;color:#8b949e;margin-bottom:6px;letter-spacing:.04em">🏢 이런 회사예요</div><div style="white-space:pre-wrap;line-height:1.7;color:#c9d1d9;margin-bottom:14px">${esc(sum)}</div></div>`;
  if (rep && (rep.summary || '').trim()) {
    bodyHtml += introHtml(rep.summary.trim());
  } else if (p.code) {                          // 최근 리포트 창에서 밀려난 보유 종목 — 종목별 폴백(#196)
    fetch('/api/fund-report?code=' + encodeURIComponent(p.code)).then(r => r.json()).then(rows => {
      const sum = rows && rows[0] && (rows[0].summary || '').trim();
      const body = document.getElementById('r-body');
      if (sum && body && !document.getElementById('r-intro')) {
        const chart = document.getElementById('r-chart');   // 차트가 있으면 그 아래에
        if (chart) chart.insertAdjacentHTML('afterend', introHtml(sum));
        else body.insertAdjacentHTML('afterbegin', introHtml(sum));
      }
    }).catch(() => {});
  }
  const structured = parseStructuredReason(p.reasoning);
  bodyHtml += `<div style="font-size:11px;font-weight:700;color:#79c0ff;margin-bottom:8px;letter-spacing:.04em">🟢 봇들이 산 이유</div>`;
  if (structured) {
    bodyHtml += renderStructuredReason(structured, p.reasoning);
  } else {
    const buyReason = cipherLetters(friendlyReason(p.reasoning));
    bodyHtml += `<div style="white-space:pre-wrap;line-height:1.7;color:#c9d1d9">${esc(buyReason)}</div>`;
  }
  if (p.status === 'closed') {
    const sellReason = cipherLetters(friendlyExit((p.exit_reasoning || '').trim()));
    bodyHtml += `<div style="height:14px"></div>
                 <div style="font-size:11px;font-weight:700;color:#f85149;margin-bottom:6px;letter-spacing:.04em">🔴 매도 이유</div>
                 <div style="white-space:pre-wrap;line-height:1.7;color:#c9d1d9">${esc(sellReason || '매도 이유 정보 없음 (옛 데이터)')}</div>`;
  }
  if (p.id) {
    bodyHtml += `<div style="margin-top:14px"><button onclick="loadBuyDebate(${p.id})" style="width:100%;padding:9px;background:rgba(88,166,255,.14);border:1px solid rgba(88,166,255,.4);border-radius:8px;color:#79c0ff;font-size:11.5px;font-weight:700;cursor:pointer">🔥 봇 전체 토론 다시보기</button></div>`;
  }
  document.getElementById('r-body').innerHTML = bodyHtml;
  if (p.code) loadPriceChart(p);

  document.getElementById('reason-modal').classList.add('open');
}
// ── 주가 차트 (#230) — 토스식: 진입가 기준 위 빨강/아래 파랑 · ▲진입 · 청산 종목은 ▼ ──
async function loadPriceChart(p) {
  const box = document.getElementById('r-chart');
  if (!box) return;
  try {
    const d = await (await fetch('/api/price-history?code=' + encodeURIComponent(p.code))).json();
    if (!d.found || !d.closes || d.closes.length < 2) { box.remove(); return; }
    box.innerHTML = renderPriceChart(d.dates, d.closes, p);
  } catch (e) { box.remove(); }
}
function renderPriceChart(dates, closes, p) {
  const W = 560, H = 190, L = 8, R = 60, T = 14, B = 20;
  const isUS = p.market === 'US';
  const fmt = v => isUS ? '$' + Number(v).toLocaleString('en-US', {maximumFractionDigits: 2})
                        : '₩' + Math.round(v).toLocaleString();
  const entry = Number(p.entry_price) || null;
  const n = closes.length;
  const vals = entry ? closes.concat([entry]) : closes;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.07; lo -= pad; hi += pad;
  const x = i => L + (W - L - R) * i / (n - 1);
  const y = v => T + (H - T - B) * (hi - v) / (hi - lo);
  const idxOf = ts => {                                  // 날짜 → 가장 가까운 거래일 인덱스
    const day = (ts || '').slice(0, 10);
    if (!day) return null;
    for (let i = 0; i < n; i++) if (dates[i] >= day) return i;
    return n - 1;
  };
  // 선 — 진입가 기준으로 빨강/파랑 분할 (교차 구간은 교점에서 쪼갬)
  let redD = '', blueD = '', prev = null;
  const base = entry ?? closes[0];
  for (let i = 0; i < n; i++) {
    const pt = {x: x(i).toFixed(1), xr: x(i), y: y(closes[i]).toFixed(1), v: closes[i]};
    if (prev) {
      if ((prev.v - base) * (pt.v - base) < 0) {
        const t = (base - prev.v) / (pt.v - prev.v);
        const mx = (prev.xr + (pt.xr - prev.xr) * t).toFixed(1), my = y(base).toFixed(1);
        const a = ` M${prev.x} ${prev.y} L${mx} ${my}`, b = ` M${mx} ${my} L${pt.x} ${pt.y}`;
        if (prev.v >= base) { redD += a; blueD += b; } else { blueD += a; redD += b; }
      } else {
        const seg = ` M${prev.x} ${prev.y} L${pt.x} ${pt.y}`;
        if ((prev.v + pt.v) / 2 >= base) redD += seg; else blueD += seg;
      }
    }
    prev = pt;
  }
  let s = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">`;
  if (entry) {
    s += `<line x1="${L}" y1="${y(entry).toFixed(1)}" x2="${W - R + 4}" y2="${y(entry).toFixed(1)}" stroke="#8b949e" stroke-width="1" stroke-dasharray="4 3" opacity="0.55"/>`;
    s += `<text x="${W - R + 7}" y="${(y(entry) + 3.5).toFixed(1)}" font-size="9.5" fill="#8b949e">진입 ${fmt(entry)}</text>`;
  }
  if (blueD) s += `<path d="${blueD.trim()}" fill="none" stroke="#58a6ff" stroke-width="1.8" stroke-linecap="round"/>`;
  if (redD) s += `<path d="${redD.trim()}" fill="none" stroke="#f85149" stroke-width="1.8" stroke-linecap="round"/>`;
  const ei = idxOf(p.opened_at);
  if (ei !== null && entry) {
    const ex = x(ei).toFixed(1), ey = y(entry);
    s += `<path d="M${ex} ${(ey - 6).toFixed(1)} l5 8 h-10 z" fill="#f85149" stroke="#0d1117" stroke-width="1"/>`;
  }
  if (p.status === 'closed' && p.exit_price) {
    const ci = idxOf(p.closed_at);
    if (ci !== null) {
      const cx = x(ci).toFixed(1), cy = y(Number(p.exit_price));
      s += `<path d="M${cx} ${(cy + 6).toFixed(1)} l5 -8 h-10 z" fill="#58a6ff" stroke="#0d1117" stroke-width="1"/>`;
    }
  }
  const last = closes[n - 1], lastWin = last >= base;
  s += `<text x="${W - R + 7}" y="${(y(last) + 3.5).toFixed(1)}" font-size="10" font-weight="700" fill="${lastWin ? '#f85149' : '#58a6ff'}">${fmt(last)}</text>`;
  [0, Math.floor((n - 1) / 2), n - 1].forEach((i, k) => {
    const anchor = k === 0 ? 'start' : (k === 2 ? 'end' : 'middle');
    s += `<text x="${x(i).toFixed(1)}" y="${H - 5}" font-size="9" fill="#6e7681" text-anchor="${anchor}">${dates[i].slice(5).replace('-', '/')}</text>`;
  });
  s += `</svg><div style="font-size:9.5px;color:#6e7681;margin-top:2px">최근 6개월 일봉 종가 · ▲ 매수${p.status === 'closed' ? ' · ▼ 매도' : ''} (가상 포트폴리오 기준)</div>`;
  return s;
}
async function loadBuyDebate(posId) {
  const body = document.getElementById('r-body');
  body.innerHTML = '<div style="color:#6e7681;font-size:11px;padding:10px">봇 토론 이미지 준비 중… (처음 한 번만 생성)</div>';
  try {
    const r = await fetch('/api/buy-debate-image?pos_id=' + posId);
    const d = await r.json();
    const urls = d.urls || (d.url ? [d.url] : []);
    if (!d.found || !urls.length) {
      body.innerHTML = '<div style="color:#6e7681;font-size:11px;padding:10px">이 매수의 토론 기록을 찾지 못했어요 (옛 데이터일 수 있음).</div>';
      return;
    }
    document.getElementById('reason-box').classList.add('wide');  // 가로로 넓게
    body.innerHTML =
      `<div style="display:flex;gap:12px;align-items:flex-start">` +
      urls.map((u, i) => `<img src="${u}" style="flex:1;min-width:0;width:100%;border-radius:8px;display:block" alt="봇 토론 ${i + 1}/${urls.length}">`).join('') +
      `</div>` +
      `<div style="font-size:10px;color:#6e7681;margin-top:8px;text-align:center">길게 눌러/우클릭으로 이미지 저장 (${urls.length}장)${d.cached ? ' · 캐시됨' : ''}</div>`;
  } catch (e) {
    body.innerHTML = '<div style="color:#f85149;font-size:11px;padding:10px">불러오기 실패</div>';
  }
}
function closeReason() {
  document.getElementById('reason-modal').classList.remove('open');
  document.getElementById('reason-box').classList.remove('wide');
}
document.addEventListener('keydown', e => { if(e.key==='Escape') { closeReason(); closeSchedule(); closeDonationModal(); closeChatLog(); } });

function fmtNum(n) {
  if (n == null) return '—';
  if (n >= 1e9) return (n/1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}

const USD_TO_KRW = 1380;  // 백엔드와 동일

function fmtKrw(n) {
  if (n == null || isNaN(n)) return '₩0';
  return '₩' + Math.round(n).toLocaleString('ko-KR');
}
function fmtTokens(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2) + 'B 토큰';
  if (n >= 1e6) return (n/1e6).toFixed(2) + 'M 토큰';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K 토큰';
  return Math.round(n) + ' 토큰';
}

async function loadTokenUsage(preT, preD) {
  try {
    let d = preT, dn = preD;
    if (!d) {
      const [tr, dr] = await Promise.all([
        fetch('/api/token-usage'),
        fetch('/api/donations'),
      ]);
      if (!tr.ok) return;
      d = await tr.json();
      dn = dr.ok ? await dr.json() : { total_krw: 0, total_usd: 0, total_tokens: 0 };
    }

    const t = d.today || {};
    const state = d.set_state || 'idle';

    // 세트 상태별 게이지·상태·텍스트 매핑
    // 핵심: 토큰은 5시간 윈도우로 자동 회복 — 세트 사이클에 묶어 시각화
    //   running: 세트 진행 중 (토큰 사용 중) — 노랑
    //   polling: 세트 종료 후 회복 대기 — 회색·천천히 펄스
    //   recovered: 회복 감지 직후 1시간 — 초록 풀
    //   exhausted: 소진 — 빨강
    //   idle: 평소 — 초록 풀
    // 상태 라벨 (이모지 + 텍스트) — 의미 전달용
    const STATE_LABEL = {
      'main_running':       '메인 사이클 진행',
      'sub_running':        '서브 사이클 진행',
      'post_check_running': '보유 점검 진행',
      'polling':            '세트 종료 · 회복 대기',
      'recovered':          '✨ 토큰 윈도우 회복',
      'idle':               '봇 활동 가능',
      'exhausted':          '봇 활동 정지 (토큰 소진)',
    };
    const info = { label: STATE_LABEL[state] || '봇 활동 가능' };

    const fill = document.getElementById('tb-gauge-fill');
    fill.className = 'tb-gauge-fill';
    const cap = d.capacity || {};
    const estMax = cap.estimated_max_cost_usd || 0;
    const pctRemain = cap.pct_remaining || 0;

    // 색은 남은 % 기준 (calibrate 안 됐으면 unset)
    let colorClass = 'ok';
    if (state === 'recovered') {
      colorClass = 'recovered';  // 빛나는 초록 펄스 (회복 강조)
    } else if (state === 'polling') {
      colorClass = 'polling';    // 회색 흐름
    } else if (state === 'exhausted') {
      colorClass = 'danger';     // 빨강 깜빡
    } else if (estMax <= 0) {
      colorClass = 'unset';      // 한도 미설정
    } else {
      // 남은 % 기준 단계
      if (pctRemain <= 15) colorClass = 'danger';
      else if (pctRemain <= 35) colorClass = 'warn';
      else if (pctRemain <= 60) colorClass = 'caution';
      else colorClass = 'ok';
    }
    fill.classList.add(colorClass);

    // 게이지 길이 = 남은 비율 (calibrate된 경우만), 그 외는 풀
    if (estMax > 0 && state !== 'recovered' && state !== 'polling') {
      fill.style.width = Math.max(3, pctRemain) + '%';
    } else {
      fill.style.width = '100%';
    }

    // 라벨 — 현재 세트 사용량 + 누적
    const remainEl = document.getElementById('tb-remain');
    const remainSubEl = document.getElementById('tb-remain-sub');
    const totalEl = document.getElementById('tb-total');
    const calls = t.calls || 0;
    const cost = t.cost_usd || 0;
    if (estMax > 0) {
      // 한도 calibrate된 경우: '$X.XX / $Y 한도 (Z%)'
      remainEl.textContent = `$${cost.toFixed(2)}`;
      remainSubEl.textContent = ` / $${estMax.toFixed(0)} 한도`;
      totalEl.textContent = `${cap.pct_used.toFixed(1)}% 사용`;
    } else {
      // 한도 모름: '콜 수 · $X' + 한도 calibrate 안내
      remainEl.textContent = `${fmtNum(calls)}콜`;
      remainSubEl.textContent = ` · $${cost.toFixed(2)}`;
      totalEl.textContent = '한도 미설정';
    }
    // 회복/polling 시점은 sub에 표시
    let subText = info.label;
    if (state === 'recovered' && d.last_recovery_at > 0) {
      const ago = Math.round((Date.now() / 1000 - d.last_recovery_at) / 60);
      subText = `${info.label} · ${ago}분 전`;
    } else if (state === 'polling' && d.last_set_ended_at > 0) {
      const ago = Math.round((Date.now() / 1000 - d.last_set_ended_at) / 60);
      subText = `${info.label} · 세트 종료 ${ago}분`;
    }

    // 보조 — 상태 라벨 (회복/polling 경과 시간 포함)
    document.getElementById('tb-sub').textContent = subText;

    // 상태 뱃지
    const s = document.getElementById('tb-status');
    s.innerHTML = '<span class="tb-status-dot"></span>' + info.label;
    s.className = 'tb-status' + (info.dotClass ? ' ' + info.dotClass : '');
    // 소진 시 박스 펄스 + 안내문
    const box = document.getElementById('token-box');
    const tag = document.getElementById('tb-tagline');
    box.classList.toggle('exhausted-warn', !!d.exhausted);
    if (d.exhausted) {
      tag.textContent = '토큰 소진 — 충전 시 자동 재개됩니다.';
    } else {
      tag.textContent = '토큰이 떨어지면 봇 활동도 중단됩니다.';
    }
    // 마지막 충전 정보 (최근 1건)
    const lastEl = document.getElementById('tb-last-donation');
    const last = (dn.recent || [])[0];
    if (last) {
      const lt = new Date(last.added_at + 'Z');
      const diffMs = Date.now() - lt.getTime();
      let rel;
      if (diffMs < 60000) rel = '방금 전';
      else if (diffMs < 3600000) rel = `${Math.floor(diffMs/60000)}분 전`;
      else if (diffMs < 86400000) rel = `${Math.floor(diffMs/3600000)}시간 전`;
      else rel = `${Math.floor(diffMs/86400000)}일 전`;
      const noteHtml = last.note ? ` · ${esc(last.note)}` : '';
      const tokenEquiv = fmtTokens(last.amount * 80);
      lastEl.innerHTML = `<div class="ld-row"><span class="ld-label">최근 충전</span><span class="ld-tokens">(+${fmtKrw(last.amount).replace('₩','₩')})</span><span class="ld-meta">${rel}${noteHtml}</span></div>`;
      lastEl.style.display = 'block';
    } else {
      lastEl.style.display = 'none';
    }

    // owner는 후원 추가 버튼 표시
    document.getElementById('tb-owner-actions').style.display = isOwner ? 'flex' : 'none';
  } catch (e) {}
}

// 후원 모달
function openDonationModal() {
  if (!isOwner) return;
  document.getElementById('dn-amount').value = '';
  document.getElementById('dn-note').value = '';
  document.getElementById('dn-error').style.display = 'none';
  _refreshRecentDonations();
  document.getElementById('donation-modal').style.display = 'flex';
}
function closeDonationModal() {
  document.getElementById('donation-modal').style.display = 'none';
}
async function _refreshRecentDonations() {
  try {
    const r = await fetch('/api/donations');
    const d = await r.json();
    const el = document.getElementById('dn-recent');
    const recent = d.recent || [];
    if (!recent.length) {
      el.innerHTML = '<div style="font-size:11px;color:#6e7681">아직 후원 없음</div>';
      return;
    }
    const TOKENS_PER_KRW = 80;  // backend 상수와 동기
    el.innerHTML = '<div style="font-size:10.5px;font-weight:700;color:#6e7681;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em">최근 5건</div>' +
      recent.map(r => {
        const tokenEquiv = fmtTokens(r.amount * TOKENS_PER_KRW);
        return `<div style="display:flex;justify-content:space-between;align-items:baseline;font-size:11px;color:#c9d1d9;padding:3px 0">
          <span><b style="color:#e2e8f0">${tokenEquiv}</b> <span style="color:#6e7681">(${fmtKrw(r.amount)})</span>${r.note ? ' <span style="color:#6e7681">· ' + esc(r.note) + '</span>' : ''}</span>
          <span style="font-size:10px;color:#6e7681">${new Date(r.added_at+'Z').toLocaleDateString('ko-KR',{month:'numeric',day:'numeric'})} <button onclick="deleteDonation(${r.id})" style="background:none;border:none;color:#f85149;cursor:pointer;font-size:11px;margin-left:4px" title="삭제">✕</button></span>
        </div>`;
      }).join('');
  } catch (e) {}
}
async function submitDonation() {
  const amount = parseFloat(document.getElementById('dn-amount').value);
  const note = document.getElementById('dn-note').value.trim();
  const err = document.getElementById('dn-error');
  if (!amount || amount <= 0) {
    err.textContent = '금액을 입력해주세요';
    err.style.display = 'block';
    return;
  }
  try {
    const r = await fetch('/api/donations', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({amount, note})
    });
    if (!r.ok) throw new Error('add failed');
    document.getElementById('dn-amount').value = '';
    document.getElementById('dn-note').value = '';
    err.style.display = 'none';
    await _refreshRecentDonations();
    await loadTokenUsage();
  } catch (e) {
    err.textContent = '추가 실패 — owner 권한 확인';
    err.style.display = 'block';
  }
}
async function deleteDonation(id) {
  if (!confirm('이 후원 항목 삭제?')) return;
  try {
    await fetch('/api/donations/' + id, {method: 'DELETE'});
    await _refreshRecentDonations();
    await loadTokenUsage();
  } catch (e) {}
}

function copyAccount() {
  const txt = document.getElementById('tb-account').textContent.trim();
  navigator.clipboard?.writeText(txt).then(() => {
    const el = document.getElementById('tb-account');
    const orig = el.textContent;
    el.textContent = '✓ 복사됨';
    setTimeout(() => { el.textContent = orig; }, 1200);
  });
}
function toggleTokenBox(e) {
  // .tb-detail 내부 클릭은 무시 (계좌 복사 등 펼친 상태 유지)
  if (e && e.target && e.target.closest('.tb-detail')) return;
  document.getElementById('token-box').classList.toggle('collapsed');
}

// ───── 일정표 모달 ─────
// _kstNow(): Date 객체 안의 UTC 시각이 실제 KST와 같게 만듦.
// 표시 시 timeZone: 'UTC' 옵션으로 그 시각 그대로 보여주면 KST로 보임.
function _kstNow() {
  return new Date(Date.now() + 9 * 3600000);
}
function _nextKstWeekdayAt(hours, minute = 0) {
  // 다음 평일 시각 (KST). hours는 number 또는 number[] 모두 허용.
  const hs = Array.isArray(hours) ? hours : [hours];
  const n = _kstNow();
  let best = null;
  for (const h of hs) {
    const t = new Date(n);
    t.setUTCHours(h, minute, 0, 0);
    if (t <= n) t.setUTCDate(t.getUTCDate() + 1);
    while (t.getUTCDay() === 0 || t.getUTCDay() === 6) {
      t.setUTCDate(t.getUTCDate() + 1);
    }
    if (!best || t < best) best = t;
  }
  return best;
}
function _nextKstDailyAt(hours, minute = 0) {
  // 다음 hours 중 가장 빠른 시각 (요일 무관, KST)
  const n = _kstNow();
  let best = null;
  for (const h of hours) {
    const t = new Date(n);
    t.setUTCHours(h, minute, 0, 0);
    if (t <= n) t.setUTCDate(t.getUTCDate() + 1);
    if (!best || t < best) best = t;
  }
  return best;
}
function _fmtKstRelative(target) {
  const n = _kstNow();
  const diffMs = target - n;
  const h = Math.floor(diffMs / 3600000);
  const m = Math.floor((diffMs % 3600000) / 60000);
  const at = target.toLocaleString('ko-KR', {
    timeZone: 'UTC',  // 이미 KST 보정된 Date
    month: 'numeric', day: 'numeric', weekday: 'short',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
  let rel;
  if (h >= 24) rel = `${Math.floor(h/24)}일 ${h%24}시간 후`;
  else if (h >= 1) rel = `${h}시간 ${m}분 후`;
  else if (m >= 1) rel = `${m}분 후`;
  else rel = '곧';
  return { abs: at, rel };
}

function openSchedule() {
  if (window.__FUND_MODE__) { _openFundSchedule(); return; }
  // 현재 활동(activity / phase / running / 요일)에 따라 active 표시
  let activeKey = null;
  const wd = _kstNow().getUTCDay();  // 0=일, 6=토
  const isWeekend = (wd === 0 || wd === 6);
  if (window._lastState) {
    const s = window._lastState;
    const m = (s.market === 'US') ? 'us' : 'kr';
    if (s.activity === 'watchlist') activeKey = 'sub_' + m;
    else if (s.activity === 'holdings_review') activeKey = 'holdings';
    else if (s.running && (s.phase === 'sector_discussion' || s.phase === 'stock_analysis')) activeKey = 'main_' + m;
    else if (isWeekend) activeKey = 'rest';
    // running=false + 평일 + phase가 stock_analysis 등 → 정지 상태. active 표시 없음.
  } else if (isWeekend) {
    activeKey = 'rest';
  }

  const items = [
    {
      key: 'main_kr', title: '🇰🇷 국장 메인 (장기)', time: '평일 06:00',
      desc: '위원회 5봇이 한국 섹터 판단 → 과반 매수',
      next: _nextKstWeekdayAt(6, 0),
    },
    {
      key: 'sub_kr', title: '🇰🇷 국장 트레이딩', time: '평일 09:05',
      desc: '규칙엔진(모멘텀·평균회귀) 스캔 → 신호 매수',
      next: _nextKstWeekdayAt(9, 5),
    },
    {
      key: 'main_us', title: '🇺🇸 미장 메인 (장기)', time: '평일 18:00',
      desc: '위원회 5봇이 미국 섹터 판단 → 과반 매수',
      next: _nextKstWeekdayAt(18, 0),
    },
    {
      key: 'sub_us', title: '🇺🇸 미장 트레이딩', time: '평일 22:30~23:30',
      desc: '규칙엔진(모멘텀·평균회귀) 스캔 → 신호 매수',
      next: _nextKstWeekdayAt(22, 30),
    },
    {
      key: 'holdings', title: '보유 점검', time: '각 메인 완료 직후',
      desc: '−20% 손절 + 위원회 청산 점검 (해당 시장)',
      next: null,
    },
    {
      key: 'rest', title: '주말 휴식', time: '토 · 일',
      desc: '모든 사이클 정지 · 월 06:00 재개',
      next: null,
    },
  ];

  const now = _kstNow();
  document.getElementById('sched-now').textContent =
    '현재 ' + now.toLocaleString('ko-KR', { timeZone: 'UTC', month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) + ' (KST)';

  const list = document.getElementById('sched-list');
  list.innerHTML = items.map(it => {
    const cls = (activeKey === it.key) ? 'sched-item active' : 'sched-item';
    const next = it.next ? _fmtKstRelative(it.next) : null;
    const nextHtml = next ? `<div class="sched-next">▶ ${next.abs} · ${next.rel}</div>` : '';
    const activeHtml = (activeKey === it.key) ? '<div class="sched-next">● 지금 진행 중</div>' : nextHtml;
    return `
      <div class="${cls}">
        <div class="sched-row">
          <span class="sched-title">${it.title}</span>
          <span class="sched-time">${it.time}</span>
        </div>
        <div class="sched-desc">${it.desc}</div>
        ${activeHtml}
      </div>`;
  }).join('');

  document.getElementById('schedule-modal').classList.add('open');
}
function closeSchedule() {
  document.getElementById('schedule-modal').classList.remove('open');
}
function _openFundSchedule() {   // 워크데이 모델 — 고정 시간표 폐지, 출근제(2026-08 개편)
  const now = _kstNow();
  document.getElementById('sched-now').textContent =
    '현재 ' + now.toLocaleString('ko-KR', { timeZone: 'UTC', month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) + ' (KST)';
  const items = [
    { title: '🌅 출근 · 아침 브리핑', time: '평일 05:30~', next: _nextKstWeekdayAt(5),
      desc: 'Z 거시 브리핑(시장 국면·최신 이슈) → F 병목 후보 큐레이션(여러 트렌드 웹서치) → 사장님 결재함에 상신' },
    { title: '📈 대형주 점검', time: '출근 직후', next: null,
      desc: 'N 섹터별 데이터 → C·J·U 종목 판단 → D가 진입 타이밍 veto → 통과분만 매수 결재' },
    { title: '📊 D 지수 계좌 · E 리스크 점검', time: '출근 직후', next: null,
      desc: 'D가 지수(롱/인버스) 자동 스윙 · E가 포트폴리오 집중도·시나리오 리스크 점검' },
    { title: '🔍 발굴 스터디 (종일 반복)', time: '~21:30, 라운드제', next: null,
      desc: 'N·F 발굴 → C·J·F 분석 → 딥스터디(반대 논리 검증) → 며칠 관찰 → 확신 시에만 결재 상신' },
    { title: '🫡 퇴근', time: '22:00', next: null,
      desc: '토큰 소진 시엔 충전될 때까지 쉬었다 재개 · 서버가 죽어도 다음 시각에 자동 재출근' },
    { title: '💬 사장님 우선', time: '상시', next: null,
      desc: '채팅을 보내면 하던 리서치를 멈추고 대화에 집중 (유휴 5분 후 자동 재개)' },
    { title: '🗄️ 백업 · 주간 리포트', time: '매일 03:00 · 토 09:00', next: null,
      desc: 'DB 자동 백업(무결성 검증) · 주간 성과 리포트(봇 판단 채점·결재 부가가치)' },
    { title: '주말 휴식', time: '토 · 일', next: null, desc: '발굴·매매 정지 · 월 05:30 재출근' },
  ];
  document.getElementById('sched-list').innerHTML = items.map(it => {
    const next = it.next ? _fmtKstRelative(it.next) : null;
    const nextHtml = next ? `<div class="sched-next">▶ ${next.abs} · ${next.rel}</div>` : '';
    return `<div class="sched-item"><div class="sched-row"><span class="sched-title">${it.title}</span>`
      + `<span class="sched-time">${it.time}</span></div><div class="sched-desc">${it.desc}</div>${nextHtml}</div>`;
  }).join('');
  document.getElementById('schedule-modal').classList.add('open');
}

// ───── 방문 통계 모달 (owner only) ─────
async function openStats() {
  const modal = document.getElementById('stats-modal');
  const content = document.getElementById('stats-content');
  modal.classList.add('open');
  content.innerHTML = '<div class="stats-empty">불러오는 중…</div>';
  try {
    const res = await fetch('/api/admin/visits?days=30');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // 신구 둘 다 캐시 (전환형 응답 {verified, all}). 옛 평면 응답도 호환.
    window._statsData = (data && data.verified) ? data : { verified: data, all: data };
    switchStatsMode(window._statsMode || 'verified');
  } catch (e) {
    content.innerHTML = `<div class="stats-empty">통계를 불러오지 못함: ${e.message}</div>`;
  }
}
function switchStatsMode(mode) {
  window._statsMode = mode;
  const v = document.getElementById('st-tab-verified'), a = document.getElementById('st-tab-all');
  if (v) v.classList.toggle('active', mode==='verified');
  if (a) a.classList.toggle('active', mode==='all');
  const d = window._statsData || {};
  renderStats(d[mode] || d.verified || {});
}
function closeStats() {
  document.getElementById('stats-modal').classList.remove('open');
}
function renderStats(data) {
  const c = document.getElementById('stats-content');
  const t = data.totals || {};
  const bySource = data.by_source || [];
  const byDay = data.by_day || [];

  // 출처별 가로 바 — UV 최댓값 기준 비율
  const maxUv = Math.max(1, ...bySource.map(r => r.uv));
  const sourceBars = bySource.length
    ? bySource.map(r => {
        const pct = Math.round(r.uv / maxUv * 100);
        return `<div class="stats-bar-row">
          <div class="stats-bar-src">${r.source}</div>
          <div class="stats-bar-track"><div class="stats-bar-fill" style="width:${pct}%"></div></div>
          <div class="stats-bar-num" style="white-space:nowrap">${r.uv}명 · ${r.pv}회</div>
        </div>`;
      }).join('')
    : '<div class="stats-empty">아직 방문 기록 없음</div>';

  // 날짜별 신규·재방문 (스택 막대)
  const maxDayUv = Math.max(1, ...byDay.map(r => r.uv || 0));
  const dayRows = byDay.slice(0, 30).map(r => {
    const nw = Math.round((r.new_uv||0) / maxDayUv * 100);
    const rt = Math.round((r.returning_uv||0) / maxDayUv * 100);
    return `<div class="stats-day2">
       <div class="stats-day2-date">${(r.day||'').slice(5)}</div>
       <div class="stats-day2-bar"><div class="seg-new" style="width:${nw}%"></div><div class="seg-ret" style="width:${rt}%"></div></div>
       <div class="stats-day2-cnt">신규 ${r.new_uv||0} · <b style="color:#22c55e">재방문 ${r.returning_uv||0}</b> <span style="color:#6e7681">· ${r.pv||0}PV</span></div>
     </div>`;
  }).join('');
  const dayBlock = byDay.length
    ? `<div class="stats-block">
         <div class="stats-block-title">날짜별 신규·재방문 (KST · 최근 ${data.window_days||30}일)</div>
         <div class="stats-legend"><span class="lg"><i style="background:#3b82f6"></i>신규</span><span class="lg"><i style="background:#22c55e"></i>재방문</span></div>
         ${dayRows}
       </div>`
    : '';

  // ── 재방문 코호트 (D1/D7) ──
  const ret = data.retention || {};
  const cohorts = data.cohorts || [];
  const today = data.today || '';
  const dMinus = (s, n) => { const d = new Date(s + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - n); return d.toISOString().slice(0, 10); };
  const d1cut = today ? dMinus(today, 1) : '';
  const d7cut = today ? dMinus(today, 7) : '';
  const retCard = (rate, base, label) =>
    `<div class="ret-card">
       <div class="ret-num">${rate == null ? '—' : rate + '%'}</div>
       <div class="ret-label">${label}</div>
       <div class="ret-sub">${base ? base + '명 기준' : '데이터 쌓는 중'}</div>
     </div>`;
  const cohRows = cohorts.slice(0, 14).map(co => {
    const sz = co.cohort_size || 0;
    const p1 = sz ? Math.round(co.d1 / sz * 100) : 0;
    const p7 = sz ? Math.round(co.d7 / sz * 100) : 0;
    const d1c = (today && co.cohort <= d1cut) ? `${co.d1} <span style="color:#6e7681">(${p1}%)</span>` : '<span style="color:#444c56">…</span>';
    const d7c = (today && co.cohort <= d7cut) ? `${co.d7} <span style="color:#6e7681">(${p7}%)</span>` : '<span style="color:#444c56">…</span>';
    return `<div class="coh-row"><span class="coh-d">${(co.cohort||'').slice(5)}</span><span class="coh-n">${sz}</span><span class="coh-v">${d1c}</span><span class="coh-v">${d7c}</span></div>`;
  }).join('');
  const retBlock = `
    <div class="stats-block">
      <div class="stats-block-title">재방문 추적 — 관전 후 다시 오나? (코호트)</div>
      <div class="ret-summary">
        ${retCard(ret.d1_rate, ret.d1_base, 'D1 · 다음날 복귀')}
        ${retCard(ret.d7_rate, ret.d7_base, 'D7 · 7일내 복귀')}
      </div>
      <div class="coh-row coh-head"><span class="coh-d">첫방문</span><span class="coh-n">신규</span><span class="coh-v">D1</span><span class="coh-v">D7</span></div>
      ${cohRows || '<div class="stats-empty">아직 코호트 없음</div>'}
    </div>`;

  c.innerHTML = `
    <div class="stats-block">
      <div class="stats-block-title">총 합 (최근 ${data.window_days || 30}일)</div>
      <div class="stats-totals">
        <div class="stat"><div class="stat-num">${t.unique_visitors ?? 0}</div><div class="stat-label">고유 방문자 (UV)</div></div>
        <div class="stat"><div class="stat-num">${t.page_views ?? 0}</div><div class="stat-label">페이지뷰 (PV)</div></div>
        <div class="stat"><div class="stat-num" style="color:#22c55e">${t.returning_visitors ?? 0}</div><div class="stat-label">재방문자</div></div>
        <div class="stat"><div class="stat-num" style="color:#22c55e">${t.returning_rate ?? 0}%</div><div class="stat-label">재방문율</div></div>
      </div>
    </div>
    ${retBlock}
    <div class="stats-block">
      <div class="stats-block-title">출처별 (UV 많은 순)</div>
      ${sourceBars}
    </div>
    ${dayBlock}
    <div class="sched-note">
      • <b>D1</b> = 첫 방문 '다음날' 복귀율 · <b>D7</b> = 첫 방문 후 '7일 내' 복귀율 (visitor_id 추적)<br>
      • 코호트 = 첫 방문일이 같은 사람 묶음 · 아직 기간 안 지난 코호트는 <b>…</b>로 표시<br>
      • 재방문자 = 서로 다른 날(KST) <b>2일 이상</b> 방문한 사람 · 재방문율 = 재방문자 / UV<br>
      • 날짜별 신규/재방문 = 그 날 방문자 중 '첫 방문일이 그 날인지'로 구분<br>
      • 같은 방문자+출처는 24시간 내 중복 카운트 안 됨 · ?s= 없으면 <b>direct</b><br>
      • 봇·스캐너·미리보기 UA는 집계에서 제외
    </div>
  `;
}

// ───── 피드백 (방문자 → 관리자) ─────
function openFeedbackSend() {
  document.getElementById('feedback-modal').classList.add('open');
  setTimeout(() => document.getElementById('fb-textarea').focus(), 50);
}
function closeFeedbackSend() {
  document.getElementById('feedback-modal').classList.remove('open');
  document.getElementById('fb-status').textContent = '';
  document.getElementById('fb-status').className = '';
}
function updateFbCounter() {
  const ta = document.getElementById('fb-textarea');
  document.getElementById('fb-counter').textContent = `${ta.value.length} / 5000`;
}
async function sendFeedback() {
  const ta = document.getElementById('fb-textarea');
  const status = document.getElementById('fb-status');
  const btn = document.getElementById('fb-send-btn');
  const content = ta.value.trim();
  status.textContent = '';
  status.className = '';
  if (content.length < 5) {
    status.textContent = '5자 이상 입력해 주세요.';
    status.className = 'err';
    return;
  }
  btn.disabled = true;
  btn.textContent = '보내는 중…';
  // 환경에 따라 fetch가 가로채질 수 있어 3가지 메커니즘 + 3가지 path 순차 시도
  const PATHS = ['/api/feedback', '/api/contact', '/api/note'];
  let lastErr = null, ok = false;
  const tryFetch = async (p) => {
    const r = await fetch(p + '?_=' + Date.now(), {
      method: 'POST', cache: 'no-store', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
      body: JSON.stringify({content}),
    });
    if (r.ok) return {ok: true, via: 'fetch ' + p};
    const d = await r.json().catch(() => ({}));
    return {ok: false, err: `fetch ${p} → HTTP ${r.status} ${d.detail || ''}`, raw: {status: r.status, url: r.url, detail: d.detail}};
  };
  const tryXhr = (p) => new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', p + '?xhr=' + Date.now(), true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.timeout = 10000;
    xhr.onload = () => xhr.status === 200
      ? resolve({ok: true, via: 'xhr ' + p})
      : resolve({ok: false, err: `xhr ${p} → HTTP ${xhr.status} ${xhr.responseText.slice(0,80)}`});
    xhr.onerror = () => resolve({ok: false, err: `xhr ${p} → network error`});
    xhr.ontimeout = () => resolve({ok: false, err: `xhr ${p} → timeout`});
    xhr.send(JSON.stringify({content}));
  });

  // 1차: fetch 시도 (이미 가로채진 환경이면 skip)
  if (!window._fetchHijacked) {
    for (const p of PATHS) {
      try {
        const r = await tryFetch(p);
        if (r.ok) { ok = true; console.log('[feedback] ✅', r.via); break; }
        console.error('[feedback] fail', r);
        lastErr = r.err;
      } catch (e) { console.error('[feedback] fetch err', p, e); lastErr = `fetch ${p} → ${e.message}`; }
    }
  } else {
    console.log('[feedback] fetch skipped (overridden) → XHR 직행');
  }
  // 2차: XHR (fetch가 가로채진 경우 우회)
  if (!ok) {
    for (const p of PATHS) {
      const r = await tryXhr(p);
      if (r.ok) { ok = true; console.log('[feedback] ✅', r.via); break; }
      console.error('[feedback] xhr fail', r);
      lastErr = r.err;
    }
  }
  // 3차: sendBeacon (응답 못 받지만 발송은 됨)
  if (!ok && navigator.sendBeacon) {
    try {
      const blob = new Blob([JSON.stringify({content})], {type: 'application/json'});
      const sent = navigator.sendBeacon('/api/feedback?beacon=' + Date.now(), blob);
      if (sent) {
        ok = true;
        console.log('[feedback] ✅ sendBeacon (응답 못 받음, 발송만 확인)');
      }
    } catch (e) { console.error('[feedback] beacon err', e); }
  }

  try {
    if (!ok) throw new Error(lastErr || '모든 경로 실패');
    status.textContent = '✅ 보냈습니다. 감사합니다!';
    status.className = 'ok';
    ta.value = '';
    updateFbCounter();
    setTimeout(closeFeedbackSend, 1500);
  } catch (e) {
    status.textContent = `전송 실패: ${e.message}`;
    status.className = 'err';
  } finally {
    btn.disabled = false;
    btn.textContent = '보내기';
  }
}

// ───── 피드백 목록 (owner) ─────
async function openFeedbackList() {
  document.getElementById('feedback-list-modal').classList.add('open');
  await loadFeedbackList(false);
}
function closeFeedbackList() {
  document.getElementById('feedback-list-modal').classList.remove('open');
  // 닫을 때 배지 갱신 (읽음 처리 반영)
  updateFeedbackBadge();
}

async function openChatLog() {
  document.getElementById('chatlog-modal').classList.add('open');
  await loadChatLog();
}
function closeChatLog() {
  document.getElementById('chatlog-modal').classList.remove('open');
}
async function loadChatLog() {
  const content = document.getElementById('chatlog-content');
  const sub = document.getElementById('chatlog-sub');
  content.innerHTML = '불러오는 중…';
  try {
    const r = await fetch('/api/admin/chats?limit=50');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    sub.textContent = `최근 ${d.items.length}건 (사용자 질문 + 봇 답변)`;
    if (!d.items.length) {
      content.innerHTML = '<div class="stats-empty">아직 사용자 채팅이 없습니다.</div>';
      return;
    }
    content.innerHTML = d.items.map(it => {
      const tsKst = it.ts ? new Date(it.ts.replace(' ', 'T') + 'Z').toLocaleString('ko-KR',
        { month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false }) : '—';
      const answers = (it.answers || []).map(a =>
        `<div class="cl-ans"><b>${esc(a.agent)}</b> ${esc(a.content)}</div>`).join('');
      return `<div class="cl-q">
        <div class="cl-q-head"><span>#${it.round_id} · ${tsKst} KST</span><span>${(it.answers||[]).length}봇 답변</span></div>
        <div class="cl-q-text">Q. ${esc(it.question || '(질문 없음)')}</div>
        ${answers || '<div class="cl-ans" style="color:#6e7681">답변 없음</div>'}
      </div>`;
    }).join('');
  } catch (e) {
    content.innerHTML = `<div class="stats-empty">목록 불러오기 실패: ${e.message}</div>`;
  }
}
async function loadFeedbackList(unreadOnly) {
  const content = document.getElementById('fb-list-content');
  const sub = document.getElementById('fb-list-sub');
  content.innerHTML = '불러오는 중…';
  try {
    const r = await fetch('/api/admin/feedback?limit=100' + (unreadOnly ? '&unread_only=true' : ''));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    sub.textContent = `${d.items.length}건 표시 · 미읽음 ${d.unread_count}건`;
    if (!d.items.length) {
      content.innerHTML = '<div class="stats-empty">아직 피드백이 없습니다.</div>';
      return;
    }
    content.innerHTML = d.items.map(it => {
      const tsKst = new Date(it.ts.replace(' ', 'T') + 'Z').toLocaleString('ko-KR',
        { month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false });
      const unread = it.is_read ? '' : 'unread';
      const ua = (it.user_agent || '').slice(0, 60);
      return `<div class="fb-item ${unread}">
        <div class="fb-meta">
          <span>#${it.id} · ${tsKst} KST</span>
          <span style="opacity:.6">${esc(ua)}</span>
        </div>
        <div class="fb-content">${esc(it.content)}</div>
        ${it.is_read ? '' : `<div class="fb-actions">
          <button class="fb-read-btn" onclick="markFbRead(${it.id})">읽음 처리</button>
        </div>`}
      </div>`;
    }).join('');
  } catch (e) {
    content.innerHTML = `<div class="stats-empty">목록 불러오기 실패: ${e.message}</div>`;
  }
}
async function markFbRead(id) {
  try {
    await fetch(`/api/admin/feedback/read?id=${id}`, {method:'POST'});
    await loadFeedbackList(false);
  } catch (e) { /* ignore */ }
}
async function markAllFbRead() {
  if (!confirm('모든 피드백을 읽음 처리할까요?')) return;
  try {
    await fetch('/api/admin/feedback/read?id=0', {method:'POST'});
    await loadFeedbackList(false);
  } catch (e) { /* ignore */ }
}
async function updateFeedbackBadge() {
  if (!document.body.classList.contains('admin-mode')) return;
  try {
    const r = await fetch('/api/admin/feedback?limit=1');
    if (!r.ok) return;
    const d = await r.json();
    const badge = document.getElementById('fb-badge');
    if (!badge) return;
    if (d.unread_count > 0) {
      badge.textContent = d.unread_count;
      badge.style.display = 'inline-flex';
    } else {
      badge.style.display = 'none';
    }
  } catch (e) { /* ignore */ }
}

// ───── 모바일 드로어 ─────
function closeDrawers() {
  document.body.classList.remove('left-open', 'right-open', 'drawer-open');
}
function toggleLeftDrawer() {
  const b = document.body;
  const open = b.classList.contains('left-open');
  closeDrawers();
  if (!open) { b.classList.add('left-open', 'drawer-open'); }
}
function toggleRightDrawer() {
  const b = document.body;
  const open = b.classList.contains('right-open');
  closeDrawers();
  if (!open) {
    b.classList.add('right-open', 'drawer-open');
    // 첫 진입 시 기본 탭 = portfolio
    if (!b.classList.contains('right-tab-memo') && !b.classList.contains('right-tab-portfolio')) {
      b.classList.add('right-tab-portfolio');
      _updateRightTabButtons();
    }
  }
}
function switchRightTab(tab) {
  const b = document.body;
  b.classList.remove('right-tab-memo', 'right-tab-portfolio');
  b.classList.add('right-tab-' + tab);
  _updateRightTabButtons();
}
function _updateRightTabButtons() {
  const isMemo = document.body.classList.contains('right-tab-memo');
  document.querySelectorAll('.right-tab-btn').forEach(btn => {
    const label = btn.textContent;
    const target = label.includes('합의') ? 'memo' : 'portfolio';
    btn.classList.toggle('active', (target === 'memo') === isMemo);
  });
}
// ESC로 드로어 닫기
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawers(); });

let isOwner = false;
// 모드 결정: URL이 /admin 일 때만 admin 가능. / 는 항상 visitor.
const _isAdminUrl = location.pathname.startsWith('/admin');
async function loadWhoami() {
  try {
    const r = await fetch('/api/whoami');
    const d = await r.json();
    // URL이 /admin이고 쿠키 인증도 통과해야 owner
    isOwner = _isAdminUrl && !!d.owner;
    document.body.classList.toggle('readonly', !isOwner);
    document.body.classList.toggle('admin-mode', isOwner);
    // 입력창 placeholder + 비활성 처리
    const input = document.getElementById('msg-input');
    if (input) {
      const ph = isOwner ? input.dataset.ownerPlaceholder : input.dataset.readonlyPlaceholder;
      if (ph) input.placeholder = ph;
      input.readOnly = false;  // 채팅 공개 — 방문자도 입력 가능
    }
  } catch (e) {
    document.body.classList.add('readonly');
  }
}
async function ownerLogout(e) {
  if (e) e.preventDefault();
  if (!confirm('로그아웃하시겠습니까?')) return;
  try {
    await fetch('/api/owner/logout', {method: 'POST'});
    location.href = '/';  // 로그아웃 후 방문자 페이지로
  } catch (err) {}
}

// ───── visitor 모드 카운트다운 (메인·서브) ─────
// KST 기준 현재 시각·요일을 분 단위로 — 브라우저 시간대 무관
function _kstNowParts() {
  const now = new Date();
  const kstMs = now.getTime() + 9 * 3600000;
  const d = new Date(kstMs);
  return {
    weekday: d.getUTCDay(),         // 0=일 ... 6=토
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
    minOfDay: d.getUTCHours() * 60 + d.getUTCMinutes(),
  };
}
function _fmtCountdown(ms) {
  if (ms <= 0) return '곧';
  const h = ms / 3600000;
  if (h >= 24) return `${(h/24).toFixed(1)}일`;
  if (h >= 1) return `${h.toFixed(1)}h`;
  return `${Math.ceil(ms/60000)}분`;
}
function _nextMainStartMs() {
  // 다음 평일 메인 사이클: 06:00(국장) · 18:00(미장) KST
  const k = _kstNowParts();
  const slots = [6 * 60, 18 * 60];
  let best = null;
  for (const sm of slots) {
    let diff = sm - k.minOfDay;
    let wd = k.weekday;
    if (diff <= 0) { diff += 24 * 60; wd = (wd + 1) % 7; }
    // 주말이면 월요일로 건너뜀
    while (wd === 0 || wd === 6) {
      diff += 24 * 60;
      wd = (wd + 1) % 7;
    }
    if (best == null || diff < best) best = diff;
  }
  return best * 60000;
}
function _nextSubStartMs() {
  // 다음 평일 서브(규칙엔진): 09:05(국장 개장직후) · 22:30(미장 개장직후, KST 근사)
  const k = _kstNowParts();
  const slots = [9 * 60 + 5, 22 * 60 + 30];
  let best = null;
  for (const sm of slots) {
    let diff = sm - k.minOfDay;
    let wd = k.weekday;
    if (diff <= 0) { diff += 24 * 60; wd = (wd + 1) % 7; }
    // 주말이면 월요일로 건너뜀
    while (wd === 0 || wd === 6) {
      diff += 24 * 60;
      wd = (wd + 1) % 7;
    }
    if (best == null || diff < best) best = diff;
  }
  return best * 60000;
}
function updateVisitorTimers() {
  if (!document.body.classList.contains('readonly')) return;  // visitor일 때만
  const s = window._lastState || {};
  const mainEl = document.getElementById('vt-main');
  const subEl = document.getElementById('vt-sub');
  if (!mainEl || !subEl) return;

  const k = _kstNowParts();
  const isWeekend = (k.weekday === 0 || k.weekday === 6);

  // 메인: 주말이거나 봇 진행 안 되는 상태면 카운트다운, 진짜 도는 중일 때만 "진행 중"
  // 진짜 도는 조건: 평일 + running + phase=토론/분석
  const mainRunning = !isWeekend && s.running &&
                      (s.phase === 'sector_discussion' || s.phase === 'stock_analysis');
  if (mainRunning) {
    mainEl.textContent = '진행 중';
    mainEl.classList.add('running');
  } else {
    mainEl.textContent = _fmtCountdown(_nextMainStartMs());
    mainEl.classList.remove('running');
  }

  // 서브: activity=watchlist면 진행 중, 아니면 다음 슬롯
  if (s.activity === 'watchlist') {
    subEl.textContent = '진행 중';
    subEl.classList.add('running');
  } else {
    subEl.textContent = _fmtCountdown(_nextSubStartMs());
    subEl.classList.remove('running');
  }
}

// 읽기 전용일 때 호출되면 무시되도록 가드
const _origFetch = window.fetch;
window.fetch = function(input, init) {
  const url = typeof input === 'string' ? input : input.url;
  const method = (init && init.method) || 'GET';
  const isWrite = method === 'POST' || method === 'DELETE';
  // /api/visit(방문 비콘)·/api/user-message(채팅 공개)는 누구나 호출 가능 — 가드 예외
  const isOwnerEndpoint = !isOwner && isWrite && url.startsWith('/api/')
    && !url.startsWith('/api/visit') && !url.startsWith('/api/user-message');
  if (isOwnerEndpoint) {
    // 익명이 쓰기 API 호출 시도 → 조용히 거부 (403 응답 흉내)
    return Promise.resolve(new Response(
      JSON.stringify({detail: 'owner only'}),
      {status: 403, headers: {'Content-Type': 'application/json'}}
    ));
  }
  return _origFetch.call(this, input, init);
};

// ── 업데이트 내역 (가장 최근 한 줄) ──────────────────────
async function loadChangelog() {
  try {
    const r = await fetch('/static/changelog.json?_=' + Date.now());
    const data = await r.json();
    if (!Array.isArray(data) || !data.length) return;
    const latest = data[0];
    const item = (latest.items && latest.items[0]) ? latest.items[0] : '';
    const esc = s => s.replace(/</g, '&lt;');
    const el = document.getElementById('cl-line');
    el.innerHTML = `🆕 <span class="cl-date">${esc(latest.date)}</span> ${esc(item)}`;
    el.title = (latest.items || []).join('\n');  // 전체는 hover로
  } catch (_) {}
}

// 실방문 비콘 — JS가 실행됐다 = 진짜 브라우저. 봇·미리보기는 여기 못 옴.
function _sendVisitBeacon() {
  try {
    // URL은 init에서 이미 정리되므로, 먼저 캡처해 둔 _visitSource를 우선 사용
    const s = (window._visitSource || new URLSearchParams(location.search).get('s') || '').trim();
    const url = '/api/visit' + (s ? ('?s=' + encodeURIComponent(s)) : '');
    if (navigator.sendBeacon) navigator.sendBeacon(url);
    else _origFetch(url, { method: 'POST', keepalive: true, credentials: 'same-origin' });
  } catch (_) {}
}

// 첫 호출은 무조건 whoami 먼저 (다른 콜들이 fetch 가드를 통과하기 위해)
loadWhoami().then(() => {
  loadAgents(); loadMarket(); loadState(); loadConsensus(); loadPortfolio(); loadBenchmark(); syncPlayStatus(); loadTokenUsage(); loadChatQueue();
  initialLoad();
  _sendVisitBeacon();   // owner 여부 확인 후 비콘 (owner면 서버가 제외)
});
loadChangelog();
setInterval(poll, 3000);
// ── 폴링 통합 (#233): 공용 GET 8종을 /api/snapshot 하나로. 탭당 분당 ~60요청 → ~8요청 ──
async function loadSnapshot() {
  if (document.hidden) return;                       // 백그라운드 탭은 폴링 중지
  try {
    const d = await (await fetch('/api/snapshot' + (window.__FUND_MODE__ ? '?desk=fund' : ''))).json();
    loadState(d.state); syncPlayStatus(d.state); loadMarket(d.market);
    loadBenchmark(d.benchmark); loadChatQueue(d.queue); loadTokenUsage(d.token, d.donations);
    if (window.__FUND_MODE__) { if (d.fund) loadFund(d.fund); if (d.fund_report) loadFundReport(d.fund_report); }
    else { loadConsensus(d.consensus); loadPortfolio(d.portfolio); }
  } catch (e) {}
}
setInterval(loadSnapshot, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { loadSnapshot(); poll(); } });
setInterval(loadWhoami, 60000);  // 1분마다 owner 상태 재확인 (per-user — 스냅샷에 넣지 않는다)
setInterval(updateVisitorTimers, 30000);  // 30초마다 카운트다운 갱신
setInterval(updateFeedbackBadge, 60000);  // 1분마다 피드백 미읽음 배지 갱신 (admin only)
loadWhoami().then(() => setTimeout(updateFeedbackBadge, 1500));  // 첫 로드 직후 한 번

/* ── AI펀드 fund 모드: 기존 셸 재사용, Members+오른쪽 패널만 4봇으로 교체 ── */
const _fKRW = n => "₩" + Math.round(n||0).toLocaleString();
const _fPct = r => (r>=0?"+":"") + (r||0).toFixed(2) + "%";
const _fCls = r => r>0.05 ? "up" : r<-0.05 ? "down" : "flat";   // 이익 빨강/손실 파랑(KR)
function _spark(hist, color) {                                  // 수익률 스파크라인(인라인 SVG)
  const pts = (hist && hist.length) ? hist.slice() : [0];
  const arr = pts.length === 1 ? [pts[0], pts[0]] : pts;        // 단일점이면 평평하게
  const w = 120, h = 30, min = Math.min(...arr, 0), max = Math.max(...arr, 0);
  const rng = (max - min) || 1;
  const y = v => (h - 3 - ((v - min) / rng) * (h - 6)).toFixed(1);
  const line = arr.map((v, i) => `${((i / (arr.length - 1)) * w).toFixed(1)},${y(v)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`
    + `<line class="spark-zero" x1="0" y1="${y(0)}" x2="${w}" y2="${y(0)}"/>`
    + `<polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
}
function renderFundMembers(d) {
  const el = document.getElementById('member-list');
  if (!el) return;
  const working = d.session === 'working';                      // 출근이면 초록 좌측선
  el.innerHTML = '';
  const add = (name, color, model, role, ret, work) => {       // 멤버 박스(닉+역할+수익률 / 모델), 출근 시 박스 초록테두리
    const box = document.createElement('div');
    box.className = 'fm-box' + (work ? ' working' : '');
    const row = document.createElement('div');
    row.className = 'member';
    let retHtml = '';
    if (ret !== null && ret !== undefined) {                    // 픽 수익률% — 한국식 색(이익 빨강/손실 파랑)
      const col = ret > 0 ? '#f85149' : (ret < 0 ? '#58a6ff' : '#8b949e');
      retHtml = `<span class="fm-ret" style="color:${col}">${ret > 0 ? '+' : ''}${ret}%</span>`;
    }
    row.innerHTML = `<span class="dot" style="background:${color}"></span>`
      + `<span class="member-name" style="color:${color}">${esc(name)}</span>`
      + (role ? `<span class="fm-role-inline">${esc(role)}</span>` : '')
      + retHtml;
    box.appendChild(row);
    const sub = document.createElement('div');
    sub.className = 'member-sub';
    sub.innerHTML = `<span class="fm-model">${esc(model)}</span>`;
    box.appendChild(sub);
    el.appendChild(box);
  };
  (d.roster||[]).forEach(b => add(b.name || b.bot, b.color, b.model || '', b.role || '', b.ret, b.working));
  add('나', '#fbbf24', '감독', '', null, false);                // '나'(감독) 유지
}
// 대형주/발굴주 = v1 포트폴리오 패널(_renderPfAccount) 그대로 재사용, 탭만 교체
function renderFundPortfolio(d) {
  window._pfData = d.accounts || {};
  if (!window._fundDesk) window._fundDesk = '대형주';
  const t1 = document.getElementById('pf-tab-main'), t2 = document.getElementById('pf-tab-sub'),
        t3 = document.getElementById('pf-tab-all');
  if (t1) { t1.innerHTML = '📈 대형주'; t1.onclick = () => setFundDesk('대형주'); }
  if (t2) { t2.innerHTML = '🔍 발굴주'; t2.onclick = () => setFundDesk('발굴주'); }
  if (t3) t3.style.display = 'none';
  const lbl = document.getElementById('market-status-label');
  if (lbl) lbl.textContent = '시가 평가 · 각 시드 ₩50,000,000';
  setFundDesk(window._fundDesk);
}
function setFundDesk(acct) {
  window._fundDesk = acct;
  const set = (id, on) => { const e = document.getElementById(id); if (e) e.classList.toggle('active', on); };
  set('pf-tab-main', acct === '대형주'); set('pf-tab-sub', acct === '발굴주');
  if (window._pfData && window._pfData[acct]) _renderPfAccount(acct);
}
async function loadFund(pre) {
  try {
    const d = pre ?? await (await fetch('/api/fund')).json();
    window._fundData = d;
    renderFundMembers(d);
    renderFundPortfolio(d);
    renderFundSector(d.progress);
  } catch (e) {}
}
// 현재 섹터 box = 대형주 데스크 진행형 (오늘 섹터 합의 진행에서 도출)
function renderFundSector(p) {
  const sn = document.getElementById('sector-name-text'), sc = document.getElementById('sector-count'),
        sp = document.getElementById('sector-phase'), sf = document.getElementById('sector-progress-fill'),
        ss = document.getElementById('sector-stocks');
  if (!p || !p.done) {                                   // 오늘 아직 시작 전 — 데스크 안내
    if (sn) sn.textContent = '대형주 · 발굴주 데스크';
    if (sc) sc.textContent = '';
    if (sp) sp.textContent = '워크데이 · 평일 05:30 출근 → 종일 스터디 → 22시 퇴근';
    if (sf) sf.style.display = 'none';
    if (ss) ss.innerHTML = '';
    return;
  }
  const st = p.stock_total || 0, sd = p.stock_done || 0;
  if (sn) sn.textContent = p.sector || '대형주 데스크';
  if (sc) sc.textContent = `섹터 ${p.done}/${p.total}` + (p.done >= p.total ? ' ✓' : '');
  if (sp) {                                               // 진행 중이면 현재 섹터의 종목 진행, 아니면 완료 안내
    const curChip = (p.chips || []).find(x => x && x.status === 'current');
    sp.textContent = p.working
      ? `${p.sector} · 종목 ${sd}/${st}` + (curChip ? ` · ${curChip.code} 분석 중` : '')
      : `오늘 ${p.done}개 섹터 분석 완료`;
  }
  if (sf) { sf.style.display = ''; sf.style.width = Math.min(100, Math.round((st ? sd / st : 0) * 100)) + '%'; }
  if (ss) ss.innerHTML = (p.chips || []).map(x => {          // 티커 · 상태색(done 초록/current 파랑/pending) · 호버 툴팁
    const code = (typeof x === 'string') ? x : (x.code || '');
    const cls = (typeof x === 'string') ? '' : (x.status === 'done' ? ' done' : x.status === 'current' ? ' current' : '');
    const tip = (typeof x === 'string') ? '' : ((x.name || '') + (x.desc ? ' — ' + x.desc : ''));
    return `<span class="stock-badge${cls}" title="${esc(tip)}">${esc(code)}</span>`;
  }).join('');
}
function renderFundReport(reports) {
  const hdr = document.querySelector('#memo-panel .panel-header h3');
  const sub = document.querySelector('#memo-panel .panel-header p');
  if (hdr) hdr.textContent = '📋 리서치 보드';
  if (sub) sub.textContent = '후보가 뭐하는 회사인지 · 핵심 재무';
  const list = document.getElementById('memo-list');
  if (!list) return;
  if (!reports || !reports.length) {
    list.innerHTML = `<div class="fr-empty">아직 리포트가 없어요. 사이클이 돌면 A가 섹터·종목별로 채웁니다.</div>`;
    return;
  }
  // 매수이유 모달의 'A 종목소개'용 — code별 최신 리포트 캐시(최신순이라 첫 등장이 최신)
  window._fundReportByCode = {};
  reports.forEach(r => { if (r.code && (r.summary || '').trim() && !window._fundReportByCode[r.code]) window._fundReportByCode[r.code] = r; });
  const cards = reports.map(r => {                              // 최신순 그대로 렌더(발굴 사이클 중엔 발굴 카드가 위로)
    if (r.desk === '섹터합의') {                                 // 섹터 합의 카드
      const sum = (r.summary || '').trim();
      const body = sum ? `<div class="fr-op">${esc(sum)}</div>` : '';
      return `<div class="fr-card fr-sec"><div class="fr-top"><span class="fr-tk">${esc(r.name || r.code)}</span><span class="fr-tag sec">섹터 합의</span></div>${body}</div>`;
    }
    const tag = r.desk === '발굴주'                              // 종목 카드(발굴/대형 태그)
      ? `<span class="fr-tag s">발굴</span>` : `<span class="fr-tag pw">대형주</span>`;
    const nm = (r.name && r.name !== r.code) ? `<span class="fr-nm">${esc(r.name)}</span>` : '';
    let s = r.summary || '';
    if (s.length > 200) s = s.slice(0, 200) + '…';
    const sum = s ? `<div class="fr-sum">${esc(s)}</div>` : '';
    const fin = r.packet ? `<div class="fr-fin">${esc(r.packet)}</div>` : '';
    return `<div class="fr-card"><div class="fr-top"><span class="fr-tk">${esc(r.code)}</span>${nm}${tag}</div>${sum}${fin}</div>`;
  }).join('');
  list.innerHTML = `<div id="fund-report">` + cards + `</div>`;
}
async function loadFundReport(pre) {
  try { renderFundReport(pre ?? await (await fetch('/api/fund-report')).json()); } catch (e) {}
}
if (window.__FUND_MODE__) {
  document.body.classList.add('fundmode');
  const hashEl = document.getElementById('chat-hash');
  const subEl = document.getElementById('round-label');
  if (hashEl) hashEl.textContent = '# AI펀드';
  if (subEl) subEl.textContent = '인생 졸업을 향해';
  // 현재 섹터 box = 대형주 데스크 진행형 — loadFund가 renderFundSector로 채운다(loadState는 fund 미동작)
  loadFund();
  loadFundReport();   // 이후 갱신은 loadSnapshot(desk=fund)이 담당(#233)
}
// v1/v2 전환 토글 — 루트 컷오버(committee 은퇴) 후엔 숨김. 그 외엔 현재 모드 활성표시.
(function(){
  if (window.__ROOT_FUND__) {                    // 컷오버: committee 없음 → 토글 제거
    const sw = document.getElementById('view-switch');
    if (sw) sw.style.display = 'none';
    return;
  }
  const el = document.getElementById(window.__FUND_MODE__ ? 'vs-v2' : 'vs-v1');
  if (el) el.classList.add('active');
})();
