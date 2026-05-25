import { useState, useEffect, useCallback, useRef } from "react";
import "./App.css";

interface PipeMessage {
  type: "game_start" | "game_use_item" | "game_next_round" | "game_over";
  room_id: string;
  timestamp: string;
  data: any;
}

interface QualityStats {
  gridCount: string;
  avgGrid: string;
  itemCount: string;
}

type QualityKey = "total" | "q1" | "q2" | "q3" | "q4" | "q5" | "q6";

const QUALITY_KEYS: QualityKey[] = ["total", "q1", "q2", "q3", "q4", "q5", "q6"];

const QUALITY_CONFIG: Record<QualityKey, { label: string; color: string }> = {
  total: { label: "全部", color: "#aaaaaa" },
  q1: { label: "白", color: "#cccccc" },
  q2: { label: "绿", color: "#4caf50" },
  q3: { label: "蓝", color: "#2196f3" },
  q4: { label: "紫", color: "#9c27b0" },
  q5: { label: "金", color: "#ffc107" },
  q6: { label: "红", color: "#f44336" },
};

function createInitialStats(): Record<QualityKey, QualityStats> {
  const result = {} as Record<QualityKey, QualityStats>;
  for (const key of QUALITY_KEYS) {
    result[key] = { gridCount: "", avgGrid: "", itemCount: "" };
  }
  return result;
}

interface EstimationResult {
  itemCount: number;
  gridMin: number;
  gridMax: number;
}

function estimateFromAvgGrid(avgGrid: number, maxGrid: number): EstimationResult[] {
  if (avgGrid <= 0 || maxGrid <= 0) return [];

  const A_int = Math.round(avgGrid * 100);
  const results: EstimationResult[] = [];

  for (let n = 1; n <= maxGrid; n++) {
    const gMin = Math.ceil((A_int * n) / 100);
    const gMax = Math.floor(((A_int + 1) * n - 1) / 100);

    const effectiveMin = Math.max(gMin, n);
    const effectiveMax = Math.min(gMax, maxGrid);

    if (effectiveMin <= effectiveMax) {
      results.push({ itemCount: n, gridMin: effectiveMin, gridMax: effectiveMax });
    }
  }

  return results;
}

function gameAvgGrid(totalGrid: number, itemCount: number): number {
  return Math.floor((totalGrid / itemCount) * 100) / 100;
}

function App() {
  const [stats, setStats] = useState<Record<QualityKey, QualityStats>>(createInitialStats);
  const [estimationResults, setEstimationResults] = useState<{
    quality: QualityKey;
    avgGrid: number;
    results: EstimationResult[];
  } | null>(null);
  const [maxGrid, setMaxGrid] = useState(150);
  const [maxGridInput, setMaxGridInput] = useState("150");
  const [eventLog, setEventLog] = useState<string[]>([]);

  const unlistenRef = useRef<(() => void) | null>(null);

  const handleGameEvent = useCallback((msg: PipeMessage) => {
    const time = new Date().toLocaleTimeString();
    setEventLog((prev) => [...prev.slice(-50), `[${time}] ${msg.type} room=${msg.room_id}`]);

    if (msg.type === "game_start") {
      setStats(createInitialStats());
      setEstimationResults(null);
      return;
    }

    const data = msg.data;
    if (!data) return;

    const allSkillLogs: any[] = [
      ...(Array.isArray(data?.GameData?.HeroSkillLog) ? data.GameData.HeroSkillLog : []),
      ...(Array.isArray(data?.ItemSkillLog) ? data.ItemSkillLog : []),
    ];

    if (allSkillLogs.length > 0) {
      setStats((prev) => {
        const next = { ...prev };

        for (const log of allSkillLogs) {
          const { TotalHitBoxIndex, AllHitItemAvgBoxIndex, HitItemIndex } = log;

          if (Array.isArray(TotalHitBoxIndex) && TotalHitBoxIndex.length >= 7) {
            for (let i = 0; i < 7; i++) {
              const key = QUALITY_KEYS[i];
              const gc = TotalHitBoxIndex[i];
              const ag = Array.isArray(AllHitItemAvgBoxIndex) ? AllHitItemAvgBoxIndex[i] : undefined;
              const ic = Array.isArray(HitItemIndex) ? HitItemIndex[i] : undefined;

              if (gc != null && gc !== 0) {
                next[key] = {
                  ...next[key],
                  gridCount: String(gc),
                  ...(ag != null && ag !== 0 ? { avgGrid: String(ag) } : {}),
                  ...(ic != null && ic !== 0 ? { itemCount: String(ic) } : {}),
                };
              }
            }
          } else if (typeof TotalHitBoxIndex === "number" && TotalHitBoxIndex !== 0) {
            next.total = {
              ...next.total,
              gridCount: String(TotalHitBoxIndex),
              ...(typeof AllHitItemAvgBoxIndex === "number" && AllHitItemAvgBoxIndex !== 0
                ? { avgGrid: String(AllHitItemAvgBoxIndex) }
                : {}),
              ...(typeof HitItemIndex === "number" && HitItemIndex !== 0
                ? { itemCount: String(HitItemIndex) }
                : {}),
            };
          }
        }

        for (const key of QUALITY_KEYS) {
          const s = next[key];
          const gc = parseFloat(s.gridCount);
          const ag = parseFloat(s.avgGrid);
          if (gc > 0 && ag > 0) {
            next[key] = { ...s, itemCount: String(Math.round(gc / ag)) };
          }
        }

        return next;
      });
    }
  }, []);

  useEffect(() => {
    let mounted = true;

    async function setup() {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const unlisten = await listen<PipeMessage>("game-event", (event) => {
          if (!mounted) return;
          handleGameEvent(event.payload);
        });
        unlistenRef.current = unlisten;
      } catch (e) {
        console.warn("Tauri event listener not available:", e);
      }
    }

    setup();
    return () => {
      mounted = false;
      unlistenRef.current?.();
    };
  }, [handleGameEvent]);

  const handleFieldChange = useCallback(
    (quality: QualityKey, field: keyof QualityStats, value: string) => {
      setStats((prev) => {
        const next = { ...prev };
        next[quality] = { ...next[quality], [field]: value };

        if (field === "gridCount" || field === "avgGrid") {
          const gc = parseFloat(next[quality].gridCount);
          const ag = parseFloat(next[quality].avgGrid);
          if (gc > 0 && ag > 0) {
            next[quality] = { ...next[quality], itemCount: String(Math.round(gc / ag)) };
          }
        }

        return next;
      });
    },
    [],
  );

  const handleEstimate = useCallback(
    (quality: QualityKey) => {
      const avgGrid = parseFloat(stats[quality].avgGrid);
      if (!avgGrid || avgGrid <= 0) return;

      const results = estimateFromAvgGrid(avgGrid, maxGrid);
      setEstimationResults({ quality, avgGrid, results });
    },
    [stats, maxGrid],
  );

  const handleMaxGridChange = useCallback((value: string) => {
    setMaxGridInput(value);
    const num = parseInt(value, 10);
    if (!isNaN(num) && num > 0 && num <= 500) {
      setMaxGrid(num);
    }
  }, []);

  const handleReset = useCallback(() => {
    setStats(createInitialStats());
    setEstimationResults(null);
    setEventLog([]);
  }, []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>BidKing Helper</h1>
        <div className="header-controls">
          <div className="max-grid-control">
            <label>最大格数:</label>
            <input
              type="number"
              value={maxGridInput}
              onChange={(e) => handleMaxGridChange(e.target.value)}
              min={1}
              max={500}
            />
          </div>
          <button className="reset-btn" onClick={handleReset}>
            重置
          </button>
        </div>
      </header>

      <div className="quality-table">
        <div className="quality-header">
          <span className="col-label">品质</span>
          <span className="col-grid">总格数</span>
          <span className="col-avg">平均格数</span>
          <span className="col-item">物品数量</span>
          <span className="col-action">估算</span>
        </div>
        {QUALITY_KEYS.map((key) => (
          <div key={key} className="quality-row">
            <span className="quality-label" style={{ color: QUALITY_CONFIG[key].color }}>
              {QUALITY_CONFIG[key].label}
            </span>
            <input
              className="num-input"
              type="number"
              value={stats[key].gridCount}
              onChange={(e) => handleFieldChange(key, "gridCount", e.target.value)}
              placeholder="—"
            />
            <input
              className="num-input"
              type="number"
              step="0.01"
              value={stats[key].avgGrid}
              onChange={(e) => handleFieldChange(key, "avgGrid", e.target.value)}
              placeholder="—"
            />
            <input
              className="num-input item-count-input"
              type="number"
              value={stats[key].itemCount}
              onChange={(e) => handleFieldChange(key, "itemCount", e.target.value)}
              placeholder="—"
            />
            <button
              className="estimate-btn"
              onClick={() => handleEstimate(key)}
              disabled={!stats[key].avgGrid}
            >
              估算
            </button>
          </div>
        ))}
      </div>

      {estimationResults && (
        <div className="estimation-section">
          <h2 style={{ color: QUALITY_CONFIG[estimationResults.quality].color }}>
            {QUALITY_CONFIG[estimationResults.quality].label} 估算结果 (avgGrid={estimationResults.avgGrid})
          </h2>
          <div className="estimation-results">
            {estimationResults.results.length === 0 ? (
              <div className="no-results">无有效估算结果</div>
            ) : (
              estimationResults.results.map((r) => (
                <div key={r.itemCount} className="estimation-row">
                  <span className="est-item-count">件数={r.itemCount}</span>
                  <span className="est-grid-range">
                    {r.gridMin === r.gridMax
                      ? `总格数=${r.gridMin}`
                      : `总格数=${r.gridMin}~${r.gridMax}`}
                  </span>
                  <span className="est-verify">
                    {r.gridMin === r.gridMax ? (
                      <>({r.gridMin}/{r.itemCount}={gameAvgGrid(r.gridMin, r.itemCount)} ✓)</>
                    ) : (
                      <>
                        ({r.gridMin}/{r.itemCount}={gameAvgGrid(r.gridMin, r.itemCount)}
                        {r.gridMax > r.gridMin &&
                          ` ~ ${r.gridMax}/${r.itemCount}=${gameAvgGrid(r.gridMax, r.itemCount)}`}
                        )
                      </>
                    )}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      <div className="event-log">
        <h3>事件日志</h3>
        <div className="log-entries">
          {eventLog.length === 0 ? (
            <div className="log-entry log-waiting">等待游戏事件...</div>
          ) : (
            eventLog.map((log, i) => (
              <div key={i} className="log-entry">{log}</div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
