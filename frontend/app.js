// InvestRight Dashboard — React 18 SPA (CDN, no build step)
// All JSX is transpiled in-browser by Babel standalone.

const { useState, useEffect, useRef, useCallback } = React;

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const API_BASE = 'http://localhost:5001';

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function getToken() { return localStorage.getItem('ir_token'); }
function setToken(t) { localStorage.setItem('ir_token', t); }
function clearToken() { localStorage.removeItem('ir_token'); }

async function apiFetch(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (res.status === 401) { clearToken(); window.location.reload(); throw new Error('Session expired'); }
  return res;
}

// ---------------------------------------------------------------------------
// LoginScreen
// ---------------------------------------------------------------------------
function LoginScreen({ onLogin }) {
  const [apiKey, setApiKey] = useState('');
  const [error, setError]   = useState('');
  const [loading, setLoading] = useState(false);

  async function handleLogin(e) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const res  = await fetch(`${API_BASE}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.message || 'Login failed'); return; }
      setToken(data.token);
      onLogin();
    } catch (err) {
      setError('Network error — is the backend running?');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="bg-gray-900 border border-gray-800 rounded-2xl p-8 w-full max-w-sm shadow-2xl">
        <h1 className="text-2xl font-bold text-center mb-2 text-brand">InvestRight</h1>
        <p className="text-gray-400 text-sm text-center mb-6">AI-Powered Trading Dashboard</p>
        <form onSubmit={handleLogin} className="space-y-4">
          <input
            type="password"
            placeholder="API Key"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-sm focus:outline-none focus:border-brand"
          />
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={loading || !apiKey}
            className="w-full bg-brand hover:bg-brand-dark disabled:opacity-50 text-white rounded-lg py-2 text-sm font-medium transition-colors"
          >
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NavBar
// ---------------------------------------------------------------------------
const TABS = ['Overview', 'Trade Setup', 'Portfolio', 'Trades', 'Backtest', 'Observability', 'Settings'];

function NavBar({ activeTab, setActiveTab, killActive, onLogout }) {
  return (
    <nav className="bg-gray-900 border-b border-gray-800 px-4 py-2 flex items-center gap-4 flex-wrap">
      <span className="font-bold text-brand mr-2 text-lg">InvestRight</span>
      {TABS.map(t => (
        <button
          key={t}
          onClick={() => setActiveTab(t)}
          className={`text-sm px-3 py-1 rounded-lg transition-colors ${
            activeTab === t
              ? 'bg-brand text-white'
              : 'text-gray-400 hover:text-white'
          }`}
        >
          {t}
        </button>
      ))}
      <div className="ml-auto flex items-center gap-3">
        {killActive && (
          <span className="text-xs bg-red-900 text-red-300 px-2 py-1 rounded-full animate-pulse">
            KILL SWITCH ON
          </span>
        )}
        <button
          onClick={onLogout}
          className="text-xs text-gray-500 hover:text-white transition-colors"
        >
          Logout
        </button>
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Shared card / stat components
// ---------------------------------------------------------------------------
function Card({ title, children, className = '' }) {
  return (
    <div className={`bg-gray-900 border border-gray-800 rounded-xl p-4 ${className}`}>
      {title && <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">{title}</h3>}
      {children}
    </div>
  );
}

function Stat({ label, value, sub, color = 'text-white' }) {
  return (
    <div>
      <p className="text-xs text-gray-500">{label}</p>
      <p className={`text-xl font-bold ${color}`}>{value ?? '—'}</p>
      {sub && <p className="text-xs text-gray-500">{sub}</p>}
    </div>
  );
}

function Badge({ status }) {
  const map = {
    BUY:    'bg-green-900 text-green-300',
    SELL:   'bg-red-900  text-red-300',
    WAIT:   'bg-gray-800 text-gray-400',
    open:   'bg-blue-900 text-blue-300',
    closed: 'bg-gray-800 text-gray-400',
    FILLED: 'bg-green-900 text-green-300',
    FAILED: 'bg-red-900  text-red-300',
    PLACED: 'bg-yellow-900 text-yellow-300',
    PENDING:'bg-gray-800 text-gray-400',
    running:   'bg-yellow-900 text-yellow-300',
    completed: 'bg-green-900 text-green-300',
    failed:    'bg-red-900  text-red-300',
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${map[status] || 'bg-gray-800 text-gray-400'}`}>
      {status}
    </span>
  );
}

function Spinner() {
  return <div className="inline-block w-4 h-4 border-2 border-brand border-t-transparent rounded-full animate-spin" />;
}

// ---------------------------------------------------------------------------
// EquityChart (Chart.js)
// ---------------------------------------------------------------------------
function EquityChart({ data }) {
  const canvasRef = useRef(null);
  const chartRef  = useRef(null);

  useEffect(() => {
    if (!data || !data.length) return;
    const ctx = canvasRef.current.getContext('2d');
    if (chartRef.current) chartRef.current.destroy();
    chartRef.current = new Chart(ctx, {
      type: 'line',
      data: {
        labels: data.map(p => p.bar_time ? new Date(p.bar_time).toLocaleDateString() : p.bar_index),
        datasets: [{
          label: 'Equity',
          data: data.map(p => p.equity),
          borderColor: '#6366f1',
          backgroundColor: 'rgba(99,102,241,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#6b7280', maxTicksLimit: 6 }, grid: { color: '#1f2937' } },
          y: { ticks: { color: '#6b7280' }, grid: { color: '#1f2937' } },
        },
      },
    });
    return () => { if (chartRef.current) chartRef.current.destroy(); };
  }, [data]);

  return <canvas ref={canvasRef} className="w-full h-48" />;
}

// ---------------------------------------------------------------------------
// KillSwitchModal
// ---------------------------------------------------------------------------
function KillSwitchModal({ isActive, onConfirm, onCancel }) {
  const [reason, setReason] = useState('');
  const action = isActive ? 'Resume Trading' : 'Halt Trading';

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-sm shadow-2xl">
        <h2 className={`text-lg font-bold mb-2 ${isActive ? 'text-green-400' : 'text-red-400'}`}>
          {action}
        </h2>
        {!isActive && (
          <>
            <p className="text-sm text-gray-400 mb-3">
              This will immediately block all new trades. Open positions are NOT affected.
            </p>
            <input
              type="text"
              placeholder="Reason (optional)"
              value={reason}
              onChange={e => setReason(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:border-red-500"
            />
          </>
        )}
        {isActive && (
          <p className="text-sm text-gray-400 mb-4">
            Resume the trading pipeline? The system will restart analysis on the next scheduler cycle.
          </p>
        )}
        <div className="flex gap-3">
          <button
            onClick={() => onConfirm(reason)}
            className={`flex-1 py-2 rounded-lg text-sm font-medium ${
              isActive ? 'bg-green-700 hover:bg-green-600' : 'bg-red-700 hover:bg-red-600'
            } text-white transition-colors`}
          >
            {action}
          </button>
          <button
            onClick={onCancel}
            className="flex-1 py-2 rounded-lg text-sm font-medium bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Overview
// ---------------------------------------------------------------------------
function OverviewTab({ health, portfolio, killActive, setKillActive, showModal, setShowModal }) {
  const cap   = portfolio?.capital    || {};
  const pnl   = portfolio?.pnl        || {};
  const stats = portfolio?.trade_stats || {};

  async function handleKillConfirm(reason) {
    try {
      const path   = killActive ? '/resume' : '/halt';
      const body   = killActive ? {} : { reason: reason || 'Dashboard toggle', activated_by: 'dashboard' };
      const res    = await apiFetch(path, { method: 'POST', body: JSON.stringify(body) });
      const data   = await res.json();
      if (res.ok) setKillActive(!killActive);
    } catch (err) { /* ignore */ }
    setShowModal(false);
  }

  return (
    <div className="p-4 space-y-4">
      {showModal && (
        <KillSwitchModal
          isActive={killActive}
          onConfirm={handleKillConfirm}
          onCancel={() => setShowModal(false)}
        />
      )}

      {/* Status row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card>
          <Stat label="DB" value={health?.db || '—'} color={health?.db === 'connected' ? 'text-green-400' : 'text-red-400'} />
        </Card>
        <Card>
          <Stat label="Redis" value={health?.redis || '—'} color={health?.redis === 'connected' ? 'text-green-400' : 'text-red-400'} />
        </Card>
        <Card>
          <Stat label="Kill Switch" value={killActive ? 'ACTIVE' : 'OFF'} color={killActive ? 'text-red-400' : 'text-green-400'} />
        </Card>
        <Card>
          <Stat
            label="Model Accuracy"
            value={health?.model_health?.accuracy != null ? `${(health.model_health.accuracy * 100).toFixed(1)}%` : '—'}
            sub={`n=${health?.model_health?.sample_size ?? 0}`}
            color={health?.model_health?.is_healthy ? 'text-green-400' : 'text-red-400'}
          />
        </Card>
      </div>

      {/* Capital */}
      <Card title="Capital">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="Total" value={cap.total_capital != null ? `₹${Number(cap.total_capital).toLocaleString()}` : '—'} />
          <Stat label="Available" value={cap.available_capital != null ? `₹${Number(cap.available_capital).toLocaleString()}` : '—'} color="text-blue-300" />
          <Stat label="Deployed" value={cap.deployed_capital != null ? `₹${Number(cap.deployed_capital).toLocaleString()}` : '—'} color="text-yellow-300" />
          <Stat
            label="Realised P&L"
            value={pnl.total_realised_pnl != null ? `₹${Number(pnl.total_realised_pnl).toLocaleString()}` : '—'}
            color={pnl.total_realised_pnl >= 0 ? 'text-green-400' : 'text-red-400'}
          />
        </div>
      </Card>

      {/* Trade stats */}
      <Card title="Trade Statistics">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="Total Trades" value={stats.total_trades ?? '—'} />
          <Stat label="Win Rate" value={stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '—'} color="text-green-400" />
          <Stat label="Avg Win" value={stats.avg_win != null ? `₹${Number(stats.avg_win).toFixed(0)}` : '—'} color="text-green-400" />
          <Stat label="Avg Loss" value={stats.avg_loss != null ? `₹${Number(stats.avg_loss).toFixed(0)}` : '—'} color="text-red-400" />
        </div>
      </Card>

      {/* Kill switch toggle */}
      <Card title="Trading Control">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setShowModal(true)}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition-colors ${
              killActive
                ? 'bg-green-700 hover:bg-green-600 text-white'
                : 'bg-red-700 hover:bg-red-600 text-white'
            }`}
          >
            {killActive ? 'Resume Trading' : 'Halt Trading'}
          </button>
          <span className="text-sm text-gray-400">
            {killActive ? 'Trading is currently HALTED. No new positions will be opened.' : 'Trading is active. Kill switch is OFF.'}
          </span>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Portfolio
// ---------------------------------------------------------------------------
function PortfolioTab() {
  const [positions, setPositions] = useState(null);
  const [loading, setLoading]     = useState(true);

  useEffect(() => {
    apiFetch('/portfolio/positions')
      .then(r => r.json())
      .then(d => setPositions(d))
      .catch(() => setPositions({ positions: [], error: 'Failed to load' }))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 flex justify-center"><Spinner /></div>;

  const pos = positions?.positions || [];

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-gray-300">Open Positions ({pos.length})</h2>
        <span className="text-xs text-gray-500">
          Unrealised P&L: {positions?.total_unrealised_pnl != null ? `₹${Number(positions.total_unrealised_pnl).toFixed(2)}` : '—'}
        </span>
      </div>
      {pos.length === 0 ? (
        <p className="text-sm text-gray-500 italic">No open positions.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-500 border-b border-gray-800">
                <th className="text-left py-2 pr-4">Symbol</th>
                <th className="text-left py-2 pr-4">Side</th>
                <th className="text-right py-2 pr-4">Qty</th>
                <th className="text-right py-2 pr-4">Entry</th>
                <th className="text-right py-2 pr-4">SL</th>
                <th className="text-right py-2 pr-4">Target</th>
                <th className="text-right py-2 pr-4">Unrealised P&L</th>
                <th className="text-left py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {pos.map(p => (
                <tr key={p.position_id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-2 pr-4 font-medium">{p.symbol}</td>
                  <td className="py-2 pr-4"><Badge status={p.action} /></td>
                  <td className="py-2 pr-4 text-right">{p.quantity}</td>
                  <td className="py-2 pr-4 text-right">{p.entry_price}</td>
                  <td className="py-2 pr-4 text-right text-red-400">{p.stop_loss}</td>
                  <td className="py-2 pr-4 text-right text-green-400">{p.target}</td>
                  <td className={`py-2 pr-4 text-right ${Number(p.unrealised_pnl) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ₹{Number(p.unrealised_pnl || 0).toFixed(2)}
                  </td>
                  <td className="py-2"><Badge status={p.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Trades
// ---------------------------------------------------------------------------
function TradesTab() {
  const [orders, setOrders] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch('/orders')
      .then(r => r.json())
      .then(d => setOrders(d))
      .catch(() => setOrders({ orders: [] }))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 flex justify-center"><Spinner /></div>;
  const list = orders?.orders || [];

  return (
    <div className="p-4">
      <h2 className="text-sm font-semibold text-gray-300 mb-3">Orders ({list.length})</h2>
      {list.length === 0 ? (
        <p className="text-sm text-gray-500 italic">No orders yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-500 border-b border-gray-800">
                <th className="text-left py-2 pr-4">Symbol</th>
                <th className="text-left py-2 pr-4">Action</th>
                <th className="text-right py-2 pr-4">Qty</th>
                <th className="text-right py-2 pr-4">Fill Price</th>
                <th className="text-left py-2 pr-4">Mode</th>
                <th className="text-left py-2 pr-4">Status</th>
                <th className="text-left py-2">Time</th>
              </tr>
            </thead>
            <tbody>
              {list.map(o => (
                <tr key={o.order_id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-2 pr-4 font-medium">{o.symbol}</td>
                  <td className="py-2 pr-4"><Badge status={o.action} /></td>
                  <td className="py-2 pr-4 text-right">{o.quantity}</td>
                  <td className="py-2 pr-4 text-right">{o.filled_price ?? '—'}</td>
                  <td className="py-2 pr-4 text-xs text-gray-400">{o.broker_mode}</td>
                  <td className="py-2 pr-4"><Badge status={o.status} /></td>
                  <td className="py-2 text-xs text-gray-500">
                    {o.placed_at ? new Date(o.placed_at).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Backtest
// ---------------------------------------------------------------------------
function BacktestTab() {
  const [runs, setRuns]         = useState([]);
  const [loading, setLoading]   = useState(true);
  const [selected, setSelected] = useState(null);
  const [equityCurve, setEq]    = useState(null);

  // Form state
  const [symbol, setSymbol]   = useState('RELIANCE.NS');
  const [start, setStart]     = useState('2023-01-01');
  const [end, setEnd]         = useState('2024-01-01');
  const [capital, setCapital] = useState('100000');
  const [submitting, setSubmitting] = useState(false);

  function loadRuns() {
    apiFetch('/backtest/runs')
      .then(r => r.json())
      .then(d => setRuns(d.runs || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => { loadRuns(); }, []);

  async function launchBacktest(e) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res  = await apiFetch('/backtest/run', {
        method: 'POST',
        body: JSON.stringify({ symbol, start_date: start, end_date: end, initial_capital: parseFloat(capital) }),
      });
      const data = await res.json();
      if (res.ok) { setTimeout(loadRuns, 1000); }
    } finally {
      setSubmitting(false);
    }
  }

  async function selectRun(run) {
    setSelected(run);
    setEq(null);
    const res  = await apiFetch(`/backtest/runs/${run.run_id}/equity-curve`);
    const data = await res.json();
    setEq(data.equity_curve || []);
  }

  return (
    <div className="p-4 space-y-4">
      {/* Launch form */}
      <Card title="New Backtest">
        <form onSubmit={launchBacktest} className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Symbol</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm w-36 focus:outline-none focus:border-brand" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Start Date</label>
            <input type="date" value={start} onChange={e => setStart(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-brand" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">End Date</label>
            <input type="date" value={end} onChange={e => setEnd(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-brand" />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Capital</label>
            <input value={capital} onChange={e => setCapital(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm w-28 focus:outline-none focus:border-brand" />
          </div>
          <button type="submit" disabled={submitting}
            className="bg-brand hover:bg-brand-dark disabled:opacity-50 text-white px-4 py-1.5 rounded text-sm font-medium transition-colors">
            {submitting ? 'Launching…' : 'Run Backtest'}
          </button>
          <button type="button" onClick={loadRuns}
            className="bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
            Refresh
          </button>
        </form>
      </Card>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Runs list */}
        <Card title="Backtest Runs">
          {loading ? <Spinner /> : runs.length === 0 ? (
            <p className="text-sm text-gray-500 italic">No runs yet.</p>
          ) : (
            <div className="space-y-1 max-h-72 overflow-y-auto">
              {runs.map(r => (
                <div key={r.run_id}
                  onClick={() => selectRun(r)}
                  className={`flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors ${
                    selected?.run_id === r.run_id ? 'bg-brand/20 border border-brand/40' : 'hover:bg-gray-800'
                  }`}>
                  <span className="font-medium">{r.symbol}</span>
                  <span className="text-xs text-gray-400">{r.start_date} → {r.end_date}</span>
                  <Badge status={r.status} />
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Selected run details */}
        {selected && (
          <Card title={`Results — ${selected.symbol}`}>
            {selected.status === 'running' ? (
              <div className="flex items-center gap-2 text-sm text-yellow-300"><Spinner /> Running…</div>
            ) : selected.metrics ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <Stat label="Total Return" value={`${((selected.metrics.total_return_pct || 0)).toFixed(2)}%`}
                    color={selected.metrics.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'} />
                  <Stat label="Win Rate" value={`${((selected.metrics.win_rate || 0) * 100).toFixed(1)}%`} color="text-green-400" />
                  <Stat label="Sharpe" value={(selected.metrics.sharpe_ratio || 0).toFixed(2)} />
                  <Stat label="Max Drawdown" value={`${((selected.metrics.max_drawdown_pct || 0)).toFixed(2)}%`} color="text-red-400" />
                  <Stat label="Total Trades" value={selected.metrics.total_trades ?? '—'} />
                  <Stat label="Profit Factor" value={(selected.metrics.profit_factor || 0).toFixed(2)} />
                </div>
                {equityCurve && equityCurve.length > 0 && (
                  <div className="mt-2">
                    <p className="text-xs text-gray-500 mb-1">Equity Curve</p>
                    <EquityChart data={equityCurve} />
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-gray-500">No metrics available.</p>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Observability
// ---------------------------------------------------------------------------
function ObservabilityTab() {
  const [metrics, setMetrics] = useState(null);
  const [audit, setAudit]     = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      apiFetch('/observability/metrics?minutes=60').then(r => r.json()),
      apiFetch('/observability/audit?limit=50').then(r => r.json()),
    ]).then(([m, a]) => {
      setMetrics(m);
      setAudit(a);
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-6 flex justify-center"><Spinner /></div>;

  const components = metrics?.components || {};
  const events     = audit?.events || [];

  const severityColor = s => ({
    ERROR: 'text-red-400', CRITICAL: 'text-red-400',
    WARNING: 'text-yellow-400', INFO: 'text-gray-300', DEBUG: 'text-gray-500',
  })[s] || 'text-gray-400';

  return (
    <div className="p-4 space-y-4">
      <Card title="Component Latency (last 60 min)">
        {Object.keys(components).length === 0 ? (
          <p className="text-sm text-gray-500 italic">No metrics yet.</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(components).map(([name, s]) => (
              <div key={name} className="bg-gray-800 rounded-lg p-3">
                <p className="text-xs text-gray-400 mb-1 truncate">{name}</p>
                <p className="text-sm font-medium">{s.avg_ms ?? '—'}ms avg</p>
                <p className="text-xs text-gray-500">p95: {s.p95_ms ?? '—'}ms</p>
                <p className="text-xs text-gray-500">
                  {s.success_count ?? 0}/{(s.success_count ?? 0) + (s.failure_count ?? 0)} ok
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Recent Audit Events">
        <div className="space-y-1 max-h-96 overflow-y-auto text-xs font-mono">
          {events.length === 0 ? (
            <p className="text-gray-500 italic">No events.</p>
          ) : events.map((e, i) => (
            <div key={i} className="flex gap-3 py-0.5 border-b border-gray-800/40">
              <span className="text-gray-600 w-20 flex-shrink-0">
                {e.created_at ? new Date(e.created_at).toLocaleTimeString() : ''}
              </span>
              <span className={`w-16 flex-shrink-0 ${severityColor(e.severity)}`}>{e.severity}</span>
              <span className="text-gray-400 w-24 flex-shrink-0 truncate">{e.component}</span>
              <span className="text-gray-300 truncate">{e.message}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Trade Setup
// ---------------------------------------------------------------------------
function TradeSetupTab() {
  const [watchlist,    setWatchlist]    = useState([]);
  const [liveHoldings, setLiveHoldings] = useState(null);
  const [liveErr,      setLiveErr]      = useState('');
  const [loadingLive,  setLoadingLive]  = useState(false);

  // form state
  const [symbol,     setSymbol]     = useState('');
  const [capitalPct, setCapitalPct] = useState(10);
  const [formMsg,    setFormMsg]    = useState('');
  const [saving,     setSaving]     = useState(false);

  // total capital from portfolio (for ₹ display)
  const [totalCapital, setTotalCapital] = useState(0);

  async function loadWatchlist() {
    try {
      const res  = await apiFetch('/watchlist');
      const data = await res.json();
      if (res.ok) setWatchlist(data.watchlist || []);
    } catch { /* ignore */ }
  }

  async function loadPortfolioCapital() {
    try {
      const res  = await apiFetch('/portfolio');
      const data = await res.json();
      setTotalCapital(data?.capital?.total_capital || 0);
    } catch { /* ignore */ }
  }

  useEffect(() => {
    loadWatchlist();
    loadPortfolioCapital();
  }, []);

  async function fetchLiveHoldings() {
    setLoadingLive(true);
    setLiveErr('');
    try {
      const res  = await apiFetch('/portfolio/live');
      const data = await res.json();
      if (res.ok) {
        setLiveHoldings(data);
      } else {
        setLiveErr(data.error || 'Failed to fetch');
      }
    } catch {
      setLiveErr('Network error');
    } finally {
      setLoadingLive(false);
    }
  }

  function pickFromHolding(sym) {
    // Zerodha returns plain ticker (e.g. "RELIANCE") — append .NS
    const formatted = sym.includes('.') ? sym : `${sym}.NS`;
    setSymbol(formatted);
    setFormMsg('');
  }

  async function handleAdd(e) {
    e.preventDefault();
    setSaving(true);
    setFormMsg('');
    try {
      const res  = await apiFetch('/watchlist', {
        method: 'POST',
        body: JSON.stringify({ symbol: symbol.trim().toUpperCase(), capital_pct: capitalPct }),
      });
      const data = await res.json();
      if (res.ok) {
        setFormMsg(`${data.symbol} added (${data.capital_pct}% capital)`);
        setSymbol('');
        setCapitalPct(10);
        await loadWatchlist();
      } else {
        setFormMsg(`Error: ${data.error}`);
      }
    } catch {
      setFormMsg('Network error');
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(sym) {
    try {
      const res = await apiFetch(`/watchlist/${encodeURIComponent(sym)}`, { method: 'DELETE' });
      if (res.ok) await loadWatchlist();
    } catch { /* ignore */ }
  }

  const rupeeAmt = totalCapital > 0
    ? `≈ ₹${Math.round(totalCapital * capitalPct / 100).toLocaleString('en-IN')}`
    : '';

  const allocatedPct = watchlist.filter(w => w.is_active).reduce((s, w) => s + w.capital_pct, 0);

  return (
    <div className="p-4 space-y-4 max-w-3xl">

      {/* Live Zerodha Holdings */}
      <Card title="Your Zerodha Portfolio">
        <p className="text-xs text-gray-500 mb-3">
          Fetch your live holdings from Zerodha to quickly pick stocks for today's trading.
        </p>
        <button
          onClick={fetchLiveHoldings}
          disabled={loadingLive}
          className="bg-brand hover:bg-brand-dark disabled:opacity-50 text-white px-4 py-2 rounded text-sm font-medium transition-colors flex items-center gap-2"
        >
          {loadingLive ? <><Spinner /> Fetching…</> : 'Fetch Live Holdings'}
        </button>
        {liveErr && <p className="text-red-400 text-xs mt-2">{liveErr}</p>}

        {liveHoldings && (
          <div className="mt-4 space-y-3">
            {liveHoldings.note && (
              <p className="text-yellow-400 text-xs">{liveHoldings.note}</p>
            )}
            {liveHoldings.holdings && liveHoldings.holdings.length > 0 && (
              <div>
                <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider">Holdings (Delivery)</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500 border-b border-gray-800">
                        <th className="text-left py-1 pr-3">Symbol</th>
                        <th className="text-right pr-3">Qty</th>
                        <th className="text-right pr-3">Avg Price</th>
                        <th className="text-right pr-3">LTP</th>
                        <th className="text-right pr-3">P&L</th>
                        <th className="text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveHoldings.holdings.map(h => (
                        <tr key={h.symbol} className="border-b border-gray-800/40 hover:bg-gray-800/30">
                          <td className="py-1 pr-3 font-medium text-white">{h.symbol}</td>
                          <td className="text-right pr-3 text-gray-300">{h.quantity}</td>
                          <td className="text-right pr-3 text-gray-300">₹{h.avg_price?.toFixed(2)}</td>
                          <td className="text-right pr-3 text-gray-300">₹{h.last_price?.toFixed(2)}</td>
                          <td className={`text-right pr-3 font-medium ${h.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {h.pnl >= 0 ? '+' : ''}₹{h.pnl?.toFixed(0)}
                          </td>
                          <td className="text-right">
                            <button
                              onClick={() => pickFromHolding(h.symbol)}
                              className="text-brand hover:text-indigo-300 text-xs underline"
                            >
                              Select
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {liveHoldings.positions && liveHoldings.positions.length > 0 && (
              <div>
                <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider">Intraday Positions</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500 border-b border-gray-800">
                        <th className="text-left py-1 pr-3">Symbol</th>
                        <th className="text-right pr-3">Qty</th>
                        <th className="text-right pr-3">Avg Price</th>
                        <th className="text-right pr-3">LTP</th>
                        <th className="text-right pr-3">P&L</th>
                        <th className="text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {liveHoldings.positions.map(p => (
                        <tr key={p.symbol} className="border-b border-gray-800/40 hover:bg-gray-800/30">
                          <td className="py-1 pr-3 font-medium text-white">{p.symbol}</td>
                          <td className="text-right pr-3 text-gray-300">{p.quantity}</td>
                          <td className="text-right pr-3 text-gray-300">₹{p.avg_price?.toFixed(2)}</td>
                          <td className="text-right pr-3 text-gray-300">₹{p.last_price?.toFixed(2)}</td>
                          <td className={`text-right pr-3 font-medium ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {p.pnl >= 0 ? '+' : ''}₹{p.pnl?.toFixed(0)}
                          </td>
                          <td className="text-right">
                            <button
                              onClick={() => pickFromHolding(p.symbol)}
                              className="text-brand hover:text-indigo-300 text-xs underline"
                            >
                              Select
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {liveHoldings.holdings?.length === 0 && liveHoldings.positions?.length === 0 && !liveHoldings.note && (
              <p className="text-gray-500 text-xs">No holdings or positions found in your Zerodha account.</p>
            )}
          </div>
        )}
      </Card>

      {/* Add Stock Form */}
      <Card title="Add Stock to Today's Trading">
        <form onSubmit={handleAdd} className="space-y-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Symbol</label>
            <input
              type="text"
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              placeholder="e.g. RELIANCE.NS"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm focus:outline-none focus:border-brand"
            />
            <p className="text-xs text-gray-600 mt-1">Use .NS for NSE, .BO for BSE. Or click Select above.</p>
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="text-xs text-gray-500">Capital Allocation</label>
              <span className="text-sm font-bold text-white">
                {capitalPct}% {rupeeAmt && <span className="text-gray-400 font-normal text-xs">{rupeeAmt}</span>}
              </span>
            </div>
            <input
              type="range"
              min="1"
              max="100"
              value={capitalPct}
              onChange={e => setCapitalPct(Number(e.target.value))}
              className="w-full accent-indigo-500"
            />
            <div className="flex justify-between text-xs text-gray-600 mt-0.5">
              <span>1%</span>
              <span>50%</span>
              <span>100%</span>
            </div>
          </div>

          {formMsg && (
            <p className={`text-xs ${formMsg.startsWith('Error') ? 'text-red-400' : 'text-green-400'}`}>
              {formMsg}
            </p>
          )}

          <button
            type="submit"
            disabled={saving || !symbol.trim()}
            className="bg-brand hover:bg-brand-dark disabled:opacity-50 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
          >
            {saving ? 'Adding…' : 'Add to Watchlist'}
          </button>
        </form>
      </Card>

      {/* Current Watchlist */}
      <Card title={`Active Watchlist (${watchlist.filter(w => w.is_active).length} stocks)`}>
        {allocatedPct > 0 && (
          <div className="mb-3 flex items-center gap-2">
            <div className="flex-1 bg-gray-800 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${allocatedPct > 100 ? 'bg-red-500' : 'bg-brand'}`}
                style={{ width: `${Math.min(allocatedPct, 100)}%` }}
              />
            </div>
            <span className={`text-xs font-medium ${allocatedPct > 100 ? 'text-red-400' : 'text-gray-400'}`}>
              {allocatedPct.toFixed(1)}% allocated
              {allocatedPct > 100 && ' — exceeds 100%!'}
            </span>
          </div>
        )}

        {watchlist.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No stocks in watchlist. Add stocks above — the scheduler will start trading them on the next 15-min cycle.
          </p>
        ) : (
          <div className="space-y-2">
            {watchlist.map(w => (
              <div key={w.symbol} className="flex items-center justify-between py-2 border-b border-gray-800/50">
                <div className="flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full ${w.is_active ? 'bg-green-400' : 'bg-gray-600'}`} />
                  <span className="font-medium text-white text-sm">{w.symbol}</span>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <span className="text-sm font-bold text-indigo-400">{w.capital_pct}%</span>
                    {totalCapital > 0 && (
                      <span className="text-xs text-gray-500 ml-1">
                        ₹{Math.round(totalCapital * w.capital_pct / 100).toLocaleString('en-IN')}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => handleRemove(w.symbol)}
                    className="text-red-500 hover:text-red-400 text-xs transition-colors"
                    title="Remove from watchlist"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Settings
// ---------------------------------------------------------------------------
function SettingsTab({ health }) {
  const [connecting,   setConnecting]   = useState(false);
  const [kiteMsg,      setKiteMsg]      = useState('');
  const [brokerMode,   setBrokerMode]   = useState(null);
  const [modeMsg,      setModeMsg]      = useState('');
  const [togglingMode, setTogglingMode] = useState(false);

  // Read ?kite= param from URL after OAuth redirect back from Zerodha
  const urlParams  = new URLSearchParams(window.location.search);
  const kiteStatus = urlParams.get('kite');

  // Clean the URL param after reading it (only once on mount)
  useEffect(() => {
    if (kiteStatus) {
      const clean = window.location.pathname;
      window.history.replaceState({}, '', clean);
    }
    // Load current broker mode
    apiFetch('/broker/status')
      .then(r => r.json())
      .then(d => setBrokerMode(d.broker_mode || 'paper'))
      .catch(() => {});
  }, []);

  async function connectZerodha() {
    setConnecting(true);
    setKiteMsg('');
    try {
      const res  = await fetch(`${API_BASE}/kite/login`);
      const data = await res.json();
      if (res.ok && data.login_url) {
        window.location.href = data.login_url;
      } else {
        setKiteMsg(`Error: ${data.error || 'Could not get login URL'}`);
        setConnecting(false);
      }
    } catch {
      setKiteMsg('Network error — is the backend running?');
      setConnecting(false);
    }
  }

  async function toggleMode() {
    const target = brokerMode === 'live' ? 'paper' : 'live';
    setTogglingMode(true);
    setModeMsg('');
    try {
      const res  = await apiFetch('/broker/mode', {
        method: 'POST',
        body: JSON.stringify({ mode: target }),
      });
      const data = await res.json();
      if (res.ok) {
        setBrokerMode(data.broker_mode);
      } else {
        setModeMsg(data.error || 'Failed to switch mode');
      }
    } catch {
      setModeMsg('Network error');
    } finally {
      setTogglingMode(false);
    }
  }

  const kt = health?.kite_token;

  return (
    <div className="p-4 space-y-4 max-w-lg">
      <Card title="Zerodha Account Connection">
        {/* OAuth return status banner */}
        {kiteStatus === 'connected' && (
          <div className="bg-green-900/40 border border-green-700 rounded-lg px-4 py-3 mb-4 text-green-300 text-sm">
            Zerodha connected successfully! Token is active until 6:00 AM IST.
          </div>
        )}
        {kiteStatus === 'failed' && (
          <div className="bg-red-900/40 border border-red-700 rounded-lg px-4 py-3 mb-4 text-red-300 text-sm">
            Zerodha connection failed. Make sure your Redirect URL in Zerodha developer console is set to <code className="bg-gray-800 px-1 rounded">http://localhost:5001/kite/callback</code>
          </div>
        )}

        {/* Current token status */}
        <div className={`flex items-center gap-2 mb-4 text-sm ${kt?.valid ? 'text-green-400' : 'text-red-400'}`}>
          <span className={`w-2 h-2 rounded-full ${kt?.valid ? 'bg-green-400' : 'bg-red-400'}`} />
          {kt?.valid
            ? `Connected — token valid until ${kt.valid_until ? new Date(kt.valid_until).toLocaleString() : '6:00 AM IST'}`
            : 'Not connected — click below to link your Zerodha account'}
        </div>

        <button
          onClick={connectZerodha}
          disabled={connecting}
          className="w-full bg-brand hover:bg-brand-dark disabled:opacity-50 text-white py-2.5 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2"
        >
          {connecting ? <><Spinner /> Redirecting to Zerodha…</> : 'Connect Zerodha Account'}
        </button>

        {kiteMsg && <p className="text-red-400 text-xs mt-2">{kiteMsg}</p>}

        <div className="mt-4 pt-4 border-t border-gray-800 space-y-1 text-xs text-gray-500">
          <p>How it works:</p>
          <ol className="list-decimal list-inside space-y-1 text-gray-600">
            <li>Click the button above → you'll be taken to Zerodha's login page</li>
            <li>Log in with your Zerodha credentials</li>
            <li>You'll be redirected back here automatically</li>
            <li>Repeat every morning — Zerodha tokens expire at 6:00 AM IST daily</li>
          </ol>
          <p className="pt-2 text-gray-600">
            Prerequisites: Set <code className="bg-gray-800 px-1 rounded">KITE_API_SECRET</code> in your <code className="bg-gray-800 px-1 rounded">.env</code> file and set Redirect URL to <code className="bg-gray-800 px-1 rounded">http://localhost:5001/kite/callback</code> in your Zerodha developer console.
          </p>
        </div>
      </Card>

      <Card title="Broker Mode">
        <div className="flex items-center justify-between mb-3">
          <div>
            <span className={`text-sm font-bold px-3 py-1 rounded-full ${
              brokerMode === 'live' ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-400'
            }`}>
              {brokerMode ? brokerMode.toUpperCase() : '…'}
            </span>
            <p className="text-xs text-gray-500 mt-2">
              {brokerMode === 'live'
                ? 'Real orders are being placed via Zerodha'
                : 'Simulated trades — no real money at risk'}
            </p>
          </div>
          <button
            onClick={toggleMode}
            disabled={togglingMode || !brokerMode}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
              brokerMode === 'live'
                ? 'bg-gray-700 hover:bg-gray-600 text-white'
                : 'bg-green-800 hover:bg-green-700 text-green-100'
            }`}
          >
            {togglingMode ? 'Switching…' : `Switch to ${brokerMode === 'live' ? 'Paper' : 'Live'}`}
          </button>
        </div>
        {modeMsg && (
          <p className="text-red-400 text-xs mt-1">{modeMsg}</p>
        )}
        {brokerMode === 'live' && (
          <p className="text-yellow-500 text-xs mt-2">
            Live mode is active. All trades will place real orders on your Zerodha account.
          </p>
        )}
      </Card>

      <Card title="System Info">
        <div className="text-sm space-y-1 text-gray-400">
          <p>Status: <span className={`font-medium ${health?.status === 'ok' ? 'text-green-400' : 'text-yellow-400'}`}>{health?.status ?? '—'}</span></p>
          <p>Kill Switch: <span className={`font-medium ${health?.kill_switch ? 'text-red-400' : 'text-green-400'}`}>{String(health?.kill_switch ?? '—')}</span></p>
          <p>DB: <span className="text-gray-300">{health?.db ?? '—'}</span></p>
          <p>Redis: <span className="text-gray-300">{health?.redis ?? '—'}</span></p>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Root App
// ---------------------------------------------------------------------------
function App() {
  const [loggedIn, setLoggedIn]     = useState(!!getToken());
  const [activeTab, setActiveTab]   = useState('Overview');
  const [health, setHealth]         = useState(null);
  const [portfolio, setPortfolio]   = useState(null);
  const [killActive, setKillActive] = useState(false);
  const [showModal, setShowModal]   = useState(false);

  const refreshData = useCallback(() => {
    if (!getToken()) return;
    fetch(`${API_BASE}/health`)
      .then(r => r.json())
      .then(d => { setHealth(d); setKillActive(!!d.kill_switch); })
      .catch(() => {});
    apiFetch('/portfolio')
      .then(r => r.json())
      .then(d => setPortfolio(d))
      .catch(() => {});
  }, []);

  // Initial load + auto-refresh every 30 seconds
  useEffect(() => {
    if (!loggedIn) return;
    refreshData();
    const id = setInterval(refreshData, 30000);
    return () => clearInterval(id);
  }, [loggedIn, refreshData]);

  if (!loggedIn) {
    return <LoginScreen onLogin={() => { setLoggedIn(true); }} />;
  }

  function handleLogout() {
    clearToken();
    setLoggedIn(false);
  }

  return (
    <div className="min-h-screen flex flex-col">
      <NavBar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        killActive={killActive}
        onLogout={handleLogout}
      />
      <main className="flex-1 overflow-auto">
        {activeTab === 'Overview'       && <OverviewTab health={health} portfolio={portfolio} killActive={killActive} setKillActive={setKillActive} showModal={showModal} setShowModal={setShowModal} />}
        {activeTab === 'Trade Setup'    && <TradeSetupTab />}
        {activeTab === 'Portfolio'      && <PortfolioTab />}
        {activeTab === 'Trades'         && <TradesTab />}
        {activeTab === 'Backtest'       && <BacktestTab />}
        {activeTab === 'Observability'  && <ObservabilityTab />}
        {activeTab === 'Settings'       && <SettingsTab health={health} />}
      </main>
      <footer className="text-center text-xs text-gray-700 py-2">
        InvestRight · Auto-refreshes every 30s
      </footer>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
