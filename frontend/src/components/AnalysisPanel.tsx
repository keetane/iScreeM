import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { api } from "../api";
import type {
  DockingAnalysis,
  DockingAnalysisPoint,
  Project,
} from "../types";
import {
  pointMatchesPlotFilters,
  type PlotFilterCondition,
} from "./analysisFilters";

const colors = [
  "#47d7e8",
  "#65e6b4",
  "#f3b84b",
  "#c48cff",
  "#ff7f83",
  "#8db6ff",
  "#f08ad6",
  "#a5df6a",
  "#ff9b62",
  "#55b7ff",
  "#dfcc63",
  "#9c91ff",
];

type ChartPoint = {
  x: number;
  y: number;
  size?: number;
  ligandName: string;
  ligandCode: string;
  chemotypeId: string;
  jobId: string;
  jobName: string;
  color: string;
  smiles: string;
  source: DockingAnalysisPoint;
};

type ChemotypeRow = {
  id: string;
  scaffold: string;
  compounds: Set<string>;
  metrics: Map<string, number[]>;
};

type ChemotypeSortKey = "scaffold" | "metric" | "compounds";

const chemicalPropertyKeys = new Set([
  "MW",
  "XLogP",
  "TPSA",
  "RoB",
  "HBD",
  "HBA",
  "HAC",
]);

const ifpMetricKeys = new Set([
  "ifp_similarity",
  "ifp_count",
  "ifp_occurrence_count",
  "ifp_hbond_count",
  "ifp_hydrophobic_count",
  "ifp_pi_count",
]);

const numericIfpColorModes = [...ifpMetricKeys];

const scoreLabels: Record<string, string> = {
  MW: "MW",
  XLogP: "XLogP",
  TPSA: "TPSA",
  RoB: "RoB",
  HBD: "HBD",
  HBA: "HBA",
  HAC: "Heavy atom count",
  Score_per_HAC: "Score/HAC",
  Score_per_HAC2: "Score/HAC²",
  ifp_cluster_id: "IFP Cluster",
  ifp_similarity: "IFP Similarity",
  ifp_count: "IFP Count",
  ifp_occurrence_count: "IFP Occurrence Count",
  ifp_hbond_count: "H-bond Count",
  ifp_hydrophobic_count: "Hydrophobic Count",
  ifp_pi_count: "Pi Interaction Count",
};
const scoreLabel = (value: string) => scoreLabels[value] || value;

const aggregateMetric = (
  row: ChemotypeRow,
  metric: string,
  mode: "min" | "max",
) => {
  const values = row.metrics.get(metric) || [];
  if (!values.length) return undefined;
  let result = values[0];
  for (const value of values.slice(1))
    result = mode === "min" ? Math.min(result, value) : Math.max(result, value);
  return result;
};

const formatMetric = (value: number | undefined) =>
  value === undefined ? "—" : Number.isInteger(value) ? String(value) : value.toFixed(3);

const finiteNumber = (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const continuousColor = (value: number, low: number, high: number) => {
  const ratio = high === low ? 0.5 : Math.max(0, Math.min(1, (value - low) / (high - low)));
  const hue = 220 - ratio * 205;
  return `hsl(${hue} 78% 58%)`;
};

const pearson = (points: ChartPoint[]) => {
  if (points.length < 2) return undefined;
  const xMean = points.reduce((sum, point) => sum + point.x, 0) / points.length;
  const yMean = points.reduce((sum, point) => sum + point.y, 0) / points.length;
  let numerator = 0;
  let xSquare = 0;
  let ySquare = 0;
  for (const point of points) {
    const x = point.x - xMean;
    const y = point.y - yMean;
    numerator += x * y;
    xSquare += x * x;
    ySquare += y * y;
  }
  const denominator = Math.sqrt(xSquare * ySquare);
  return denominator ? numerator / denominator : undefined;
};

export function AnalysisPanel({
  project,
  scoreX,
  scoreY,
  onScoreXChange,
  onScoreYChange,
  onSelectPose,
}: {
  project?: Project;
  scoreX: string;
  scoreY: string;
  onScoreXChange: (score: string) => void;
  onScoreYChange: (score: string) => void;
  onSelectPose: (point: DockingAnalysisPoint) => void;
}) {
  const [data, setData] = useState<DockingAnalysis>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [colorMode, setColorMode] = useState("chemotype");
  const [sizeColumn, setSizeColumn] = useState("none");
  const [selectedChemotypes, setSelectedChemotypes] = useState<Set<string>>(
    new Set(),
  );
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [chemotypeMetric, setChemotypeMetric] = useState("affinity");
  const [aggregateMode, setAggregateMode] = useState<"min" | "max">("min");
  const [plotFilters, setPlotFilters] = useState<PlotFilterCondition[]>([
    { id: 1, metric: "affinity", minimum: "", maximum: "", category: "" },
  ]);
  const nextFilterId = useRef(2);
  const [chemotypeSort, setChemotypeSort] = useState<{
    key: ChemotypeSortKey;
    direction: "asc" | "desc";
  }>({ key: "compounds", direction: "desc" });
  const [selectedPoseKey, setSelectedPoseKey] = useState<string>();

  const load = async () => {
    if (!project) {
      setData(undefined);
      return;
    }
    setLoading(true);
    setError(undefined);
    try {
      const next = await api.dockingAnalysis(project.id);
      setData(next);
      const scores = next.score_columns.length
        ? next.score_columns
        : ["affinity"];
      const nextX = scores.includes(scoreX) ? scoreX : scores[0];
      onScoreXChange(nextX);
      onScoreYChange(
        scores.includes(scoreY) && scoreY !== nextX
          ? scoreY
          : scores.find((score) => score !== nextX) || nextX,
      );
      if (
        sizeColumn !== "none" &&
        !scores.includes(sizeColumn)
      )
        setSizeColumn("none");
      if (!scores.includes(chemotypeMetric)) setChemotypeMetric(scores[0]);
      const hasClusters = next.points.some((point) => point.ifp_cluster_id);
      setPlotFilters((current) =>
        current.map((filter) =>
          scores.includes(filter.metric) ||
          (filter.metric === "ifp_cluster_id" && hasClusters)
            ? filter
            : { ...filter, metric: scores[0], category: "" },
        ),
      );
      if (
        colorMode !== "chemotype" &&
        colorMode !== "job" &&
        !scores.includes(colorMode) &&
        !(colorMode === "ifp_cluster_id" && hasClusters)
      )
        setColorMode("chemotype");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setSelectedChemotypes(new Set());
    setSelectedOnly(false);
    setPlotFilters([
      { id: 1, metric: "affinity", minimum: "", maximum: "", category: "" },
    ]);
    nextFilterId.current = 2;
    setChemotypeSort({ key: "compounds", direction: "desc" });
    setSelectedPoseKey(undefined);
  }, [project?.id]);

  useEffect(() => {
    void load();
    // Selections are deliberately not dependencies: changing a control must
    // not re-fetch the complete analysis payload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  const colorByChemotype = useMemo(() => {
    const ids = [...new Set(data?.points.map((point) => point.chemotype_id) || [])].sort();
    return new Map(ids.map((id, index) => [id, colors[index % colors.length]]));
  }, [data]);

  const colorByJob = useMemo(() => {
    return new Map(
      (data?.jobs || []).map((job, index) => [
        job.id,
        colors[index % colors.length],
      ]),
    );
  }, [data]);

  const ifpClusterIds = useMemo(
    () =>
      [...new Set(data?.points.map((point) => point.ifp_cluster_id).filter((value): value is string => Boolean(value)) || [])].sort(),
    [data],
  );

  const colorByIfpCluster = useMemo(
    () =>
      new Map(
        ifpClusterIds.map((id, index) => [
          id,
          id === "No Interaction"
            ? "#64748b"
            : id === "IFP Error"
              ? "#334155"
              : colors[index % colors.length],
        ]),
      ),
    [ifpClusterIds],
  );

  const numericColorRange = useMemo(() => {
    if (!data || !ifpMetricKeys.has(colorMode)) return undefined;
    if (colorMode === "ifp_similarity") return { low: 0, high: 1 };
    const values = data.points
      .map((point) => finiteNumber(point.scores[colorMode]))
      .filter((value): value is number => value !== undefined)
      .sort((left, right) => left - right);
    if (!values.length) return undefined;
    return {
      low: values[Math.floor((values.length - 1) * 0.05)],
      high: values[Math.floor((values.length - 1) * 0.95)],
    };
  }, [colorMode, data]);

  const plottedPoints = useMemo(() => {
    if (!data) return [];
    const jobs = new Map(data.jobs.map((job) => [job.id, job]));
    return data.points.flatMap((row) => {
      const x = finiteNumber(row.scores[scoreX]);
      const y = finiteNumber(row.scores[scoreY]);
      if (x === undefined || y === undefined) return [];
      if (
        (selectedOnly && !selectedChemotypes.has(row.chemotype_id)) ||
        !pointMatchesPlotFilters(row, plotFilters)
      )
        return [];
      return [
        (() => {
          const colorCategory =
            colorMode === "job"
              ? row.job_id
              : colorMode === "ifp_cluster_id"
                ? row.ifp_cluster_id || "IFP Error"
                : colorMode === "chemotype"
                  ? row.chemotype_id
                  : row.key;
          const colorValue = finiteNumber(row.scores[colorMode]);
          const color =
            colorMode === "job"
              ? colorByJob.get(row.job_id)
              : colorMode === "ifp_cluster_id"
                ? colorByIfpCluster.get(colorCategory)
                : colorMode === "chemotype"
                  ? colorByChemotype.get(row.chemotype_id)
                  : colorValue !== undefined && numericColorRange
                    ? continuousColor(
                        colorValue,
                        numericColorRange.low,
                        numericColorRange.high,
                      )
                    : "#64748b";
          return {
          x,
          y,
          size:
            sizeColumn === "none"
              ? undefined
              : finiteNumber(row.scores[sizeColumn]),
          ligandName: row.ligand_name,
          ligandCode: row.ligand_code,
          chemotypeId: row.chemotype_id,
          jobId: row.job_id,
          jobName: jobs.get(row.job_id)?.name || row.job_id,
          color: color || "#94a3b8",
          smiles: row.smiles,
          source: row,
          };
        })(),
      ];
    });
  }, [
    colorMode,
    colorByChemotype,
    colorByIfpCluster,
    colorByJob,
    data,
    numericColorRange,
    plotFilters,
    scoreX,
    scoreY,
    selectedChemotypes,
    selectedOnly,
    sizeColumn,
  ]);

  const crosstable = useMemo(() => {
    if (!data) return [];
    const rows = new Map<string, ChemotypeRow>();
    for (const point of data.points) {
      const row = rows.get(point.chemotype_id) || {
        id: point.chemotype_id,
        scaffold: point.chemotype_smiles,
        compounds: new Set<string>(),
        metrics: new Map<string, number[]>(),
      };
      row.compounds.add(point.match_key);
      for (const [key, raw] of Object.entries(point.scores)) {
        const value = finiteNumber(raw);
        if (value === undefined) continue;
        const metricValues = row.metrics.get(key) || [];
        metricValues.push(value);
        row.metrics.set(key, metricValues);
      }
      rows.set(point.chemotype_id, row);
    }
    return [...rows.values()];
  }, [data]);

  const visibleCrosstable = useMemo(() => {
    const valueForSort = (row: ChemotypeRow) => {
      if (chemotypeSort.key === "scaffold") return row.scaffold || row.id;
      if (chemotypeSort.key === "metric")
        return aggregateMetric(row, chemotypeMetric, aggregateMode);
      return row.compounds.size;
    };
    return [...crosstable].sort((left, right) => {
      const leftValue = valueForSort(left);
      const rightValue = valueForSort(right);
      if (leftValue === undefined && rightValue !== undefined) return 1;
      if (rightValue === undefined && leftValue !== undefined) return -1;
      let result = 0;
      if (typeof leftValue === "string" && typeof rightValue === "string")
        result = leftValue.localeCompare(rightValue);
      else if (leftValue !== undefined && rightValue !== undefined)
        result = Number(leftValue) - Number(rightValue);
      return (chemotypeSort.direction === "asc" ? result : -result) ||
        left.id.localeCompare(right.id);
    });
  }, [
    aggregateMode,
    chemotypeMetric,
    chemotypeSort,
    crosstable,
  ]);

  const toggleChemotypeSort = (
    key: ChemotypeSortKey,
    defaultDirection: "asc" | "desc" = "asc",
  ) => {
    setChemotypeSort((current) => ({
      key,
      direction:
        current.key === key
          ? current.direction === "asc"
            ? "desc"
            : "asc"
          : defaultDirection,
    }));
  };

  const sortIndicator = (key: ChemotypeSortKey) =>
    chemotypeSort.key === key
      ? chemotypeSort.direction === "asc"
        ? " ↑"
        : " ↓"
      : "";

  const toggleChemotype = (id: string) => {
    setSelectedChemotypes((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const correlation = pearson(plottedPoints);
  const colorLegend = useMemo(() => {
    if (colorMode === "job")
      return (data?.jobs || []).map((job) => ({
          key: job.id,
          label: job.name,
          color: colorByJob.get(job.id) || "#94a3b8",
        }));
    if (colorMode === "ifp_cluster_id")
      return ifpClusterIds.slice(0, 50).map((id) => ({
        key: id,
        label: id,
        color: colorByIfpCluster.get(id) || "#94a3b8",
      }));
    if (ifpMetricKeys.has(colorMode) && numericColorRange)
      return [
        {
          key: "low",
          label: `Low ${formatMetric(numericColorRange.low)}`,
          color: continuousColor(
            numericColorRange.low,
            numericColorRange.low,
            numericColorRange.high,
          ),
        },
        {
          key: "high",
          label: `High ${formatMetric(numericColorRange.high)}`,
          color: continuousColor(
            numericColorRange.high,
            numericColorRange.low,
            numericColorRange.high,
          ),
        },
      ];
    return [];
  }, [
    colorByIfpCluster,
    colorByJob,
    colorMode,
    data,
    ifpClusterIds,
    numericColorRange,
  ]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!project ? (
        <div className="panel grid min-h-0 flex-1 place-items-center text-sm text-slate-500">
          Select a project in Protein &amp; Pocket to analyze its docking results.
        </div>
      ) : error ? (
        <div className="panel grid min-h-0 flex-1 place-items-center text-sm text-red-300">{error}</div>
      ) : !data || loading ? (
        <div className="panel grid min-h-0 flex-1 place-items-center text-sm text-slate-500">Loading analysis…</div>
      ) : data.jobs.length === 0 ? (
        <div className="panel grid min-h-0 flex-1 place-items-center text-center text-sm text-slate-500">
          <div>
            <div>No visible docking results are available.</div>
            <div className="mt-1 text-xs">Enable a completed docking job in Job Monitor to include it in Analysis.</div>
          </div>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(300px,0.8fr)_minmax(0,2.2fr)] gap-4">
          <section className="panel flex min-h-0 flex-col overflow-hidden">
            <div className="panel-head shrink-0">Chemotype crosstable</div>
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-full border-collapse text-xs">
                <thead className="sticky top-0 z-10 bg-[#0c1928] text-slate-500">
                  <tr>
                    <th className="border-b border-line px-3 py-2 text-left font-medium">
                      <button onClick={() => toggleChemotypeSort("scaffold")}>
                        Scaffold{sortIndicator("scaffold")}
                      </button>
                    </th>
                    <th className="min-w-40 border-b border-line px-2 py-2 text-right font-medium">
                      <div className="flex items-center justify-end gap-1">
                        <select
                          className="input h-8 min-w-24 py-1 text-[10px] normal-case"
                          value={chemotypeMetric}
                          onChange={(event) => {
                            setChemotypeMetric(event.target.value);
                            setChemotypeSort({
                              key: "metric",
                              direction: aggregateMode === "min" ? "asc" : "desc",
                            });
                          }}
                        >
                          <MetricOptions columns={data.score_columns} />
                        </select>
                        <select
                          className="input h-8 w-16 py-1 text-[10px] normal-case"
                          value={aggregateMode}
                          onChange={(event) => {
                            const mode = event.target.value as "min" | "max";
                            setAggregateMode(mode);
                            setChemotypeSort({
                              key: "metric",
                              direction: mode === "min" ? "asc" : "desc",
                            });
                          }}
                        >
                          <option value="min">Min</option>
                          <option value="max">Max</option>
                        </select>
                      </div>
                      <button
                        className="mt-1 w-full text-right"
                        onClick={() =>
                          toggleChemotypeSort(
                            "metric",
                            aggregateMode === "min" ? "asc" : "desc",
                          )
                        }
                      >
                        Sort{sortIndicator("metric")}
                      </button>
                    </th>
                    <th className="border-b border-line px-2 py-2 text-right font-medium">
                      <button onClick={() => toggleChemotypeSort("compounds", "desc")}>
                        Compounds{sortIndicator("compounds")}
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visibleCrosstable.map((row) => {
                    const selected = selectedChemotypes.has(row.id);
                    const muted = selectedChemotypes.size > 0 && !selected;
                    return (
                      <tr
                        key={row.id}
                        role="button"
                        tabIndex={0}
                        aria-pressed={selected}
                        className={`cursor-pointer border-b border-line/50 transition hover:bg-white/[0.03] ${selected ? "bg-cyan/10" : ""} ${muted ? "grayscale opacity-35" : ""}`}
                        onClick={() => toggleChemotype(row.id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            toggleChemotype(row.id);
                          }
                        }}
                      >
                        <td className="px-3 py-2" title={row.id}>
                          <div className="flex items-center gap-2">
                            <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: colorByChemotype.get(row.id) }} />
                            {row.scaffold ? (
                              <img
                                className="h-[74px] w-[150px] rounded-md bg-white object-contain"
                                src={api.structureSvgUrl(row.scaffold, 300, 148)}
                                alt="RDKit scaffold structure"
                                loading="lazy"
                              />
                            ) : (
                              <div className="grid h-[74px] w-[150px] place-items-center rounded-md border border-line text-[10px] text-slate-500">
                                Structure unavailable
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums text-slate-300">
                          {formatMetric(
                            aggregateMetric(row, chemotypeMetric, aggregateMode),
                          )}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums text-slate-300">
                          {row.compounds.size}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="shrink-0 border-t border-line bg-[#0b1725] p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                    Plot filter
                  </div>
                  <div className="mt-0.5 text-[10px] text-slate-600">
                    All conditions use AND and apply independently of X/Y axes.
                  </div>
                </div>
                <button
                  className="btn-secondary px-2 py-1 text-xs"
                  onClick={() => {
                    const used = new Set(
                      plotFilters.map((filter) => filter.metric),
                    );
                    const metric =
                      data.score_columns.find((column) => !used.has(column)) ||
                      data.score_columns[0];
                    setPlotFilters((current) => [
                      ...current,
                      {
                        id: nextFilterId.current++,
                        metric,
                        minimum: "",
                        maximum: "",
                        category: "",
                      },
                    ]);
                  }}
                >
                  Add filter
                </button>
              </div>
              <div className="max-h-40 space-y-2 overflow-auto pr-1">
                {plotFilters.map((filter) => (
                  <div
                    key={filter.id}
                    className="grid grid-cols-[minmax(0,1fr)_68px_68px_24px] items-center gap-1.5"
                  >
                    <select
                      className="input min-w-0"
                      value={filter.metric}
                      onChange={(event) =>
                        setPlotFilters((current) =>
                          current.map((item) =>
                            item.id === filter.id
                              ? {
                                  ...item,
                                  metric: event.target.value,
                                  minimum: "",
                                  maximum: "",
                                  category: "",
                                }
                              : item,
                          ),
                        )
                      }
                      aria-label="Plot filter property"
                    >
                      <MetricOptions
                        columns={data.score_columns}
                        includeIfpCluster={ifpClusterIds.length > 0}
                      />
                    </select>
                    {filter.metric === "ifp_cluster_id" ? (
                      <select
                        className="input col-span-2 min-w-0"
                        value={filter.category}
                        onChange={(event) =>
                          setPlotFilters((current) =>
                            current.map((item) =>
                              item.id === filter.id
                                ? { ...item, category: event.target.value }
                                : item,
                            ),
                          )
                        }
                        aria-label="IFP cluster filter"
                      >
                        <option value="">All clusters</option>
                        {ifpClusterIds.map((id) => (
                          <option key={id} value={id}>{id}</option>
                        ))}
                      </select>
                    ) : (
                      <>
                        <input
                          className="input min-w-0"
                          type="number"
                          step="any"
                          value={filter.minimum}
                          onChange={(event) =>
                            setPlotFilters((current) =>
                              current.map((item) =>
                                item.id === filter.id
                                  ? { ...item, minimum: event.target.value }
                                  : item,
                              ),
                            )
                          }
                          placeholder="Min"
                          aria-label={`${scoreLabel(filter.metric)} minimum`}
                        />
                        <input
                          className="input min-w-0"
                          type="number"
                          step="any"
                          value={filter.maximum}
                          onChange={(event) =>
                            setPlotFilters((current) =>
                              current.map((item) =>
                                item.id === filter.id
                                  ? { ...item, maximum: event.target.value }
                                  : item,
                              ),
                            )
                          }
                          placeholder="Max"
                          aria-label={`${scoreLabel(filter.metric)} maximum`}
                        />
                      </>
                    )}
                    <button
                      className="rounded p-1 text-slate-500 transition hover:bg-white/5 hover:text-red-300"
                      aria-label={`Remove ${scoreLabel(filter.metric)} filter`}
                      onClick={() =>
                        setPlotFilters((current) =>
                          current.filter((item) => item.id !== filter.id),
                        )
                      }
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              {plotFilters.length === 0 && (
                <div className="py-2 text-center text-xs text-slate-600">
                  No property filters.
                </div>
              )}
              <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                <label className="flex items-center gap-2 text-slate-400">
                  <input
                    type="checkbox"
                    checked={selectedOnly}
                    onChange={(event) => setSelectedOnly(event.target.checked)}
                  />
                  Selected chemotypes only
                </label>
                <div className="flex items-center gap-2">
                  <button
                    className="text-[10px] text-slate-500 transition hover:text-slate-300 disabled:cursor-default disabled:hover:text-slate-500"
                    disabled={selectedChemotypes.size === 0}
                    onClick={() => setSelectedChemotypes(new Set())}
                  >
                    {selectedChemotypes.size} selected · clear
                  </button>
                  <button
                    className="btn-secondary px-2 py-1 text-xs"
                    disabled={
                      !selectedOnly &&
                      !plotFilters.some(
                        (filter) =>
                          filter.minimum || filter.maximum || filter.category,
                      )
                    }
                    onClick={() => {
                      setSelectedOnly(false);
                      setPlotFilters((current) =>
                        current.map((filter) => ({
                          ...filter,
                          minimum: "",
                          maximum: "",
                          category: "",
                        })),
                      );
                    }}
                  >
                    Clear filter
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section className="panel flex min-h-0 flex-col overflow-hidden">
            <div className="grid shrink-0 grid-cols-2 gap-3 border-b border-line p-3 xl:grid-cols-4">
              <Control label="Y score">
                <select className="input" value={scoreY} onChange={(event) => onScoreYChange(event.target.value)}>
                  {data.score_columns.map((score) => <option key={score} value={score}>{scoreLabel(score)}</option>)}
                </select>
              </Control>
              <Control label="X score">
                <select className="input" value={scoreX} onChange={(event) => onScoreXChange(event.target.value)}>
                  {data.score_columns.map((score) => <option key={score} value={score}>{scoreLabel(score)}</option>)}
                </select>
              </Control>
              <Control label="Marker color">
                <select className="input" value={colorMode} onChange={(event) => setColorMode(event.target.value)}>
                  <option value="chemotype">Chemotype</option>
                  <option value="job">Docking job</option>
                  {ifpClusterIds.length > 0 && (
                    <option value="ifp_cluster_id">IFP Cluster</option>
                  )}
                  {numericIfpColorModes
                    .filter((metric) => data.score_columns.includes(metric))
                    .map((metric) => (
                      <option key={metric} value={metric}>{scoreLabel(metric)}</option>
                    ))}
                </select>
              </Control>
              <Control label="Marker size">
                <select className="input" value={sizeColumn} onChange={(event) => setSizeColumn(event.target.value)}>
                  <option value="none">None (uniform)</option>
                  {data.score_columns.map((score) => <option key={score} value={score}>{scoreLabel(score)}</option>)}
                </select>
              </Control>
            </div>
            <div className="flex shrink-0 items-center gap-5 border-b border-line/60 px-4 py-2 text-xs text-slate-400">
              <span><strong className="text-slate-100">{plottedPoints.length.toLocaleString()}</strong> plotted results</span>
              <span>Pearson r: <strong className="text-slate-100">{correlation === undefined ? "—" : correlation.toFixed(3)}</strong></span>
              <span className="min-w-0 truncate">{scoreLabel(scoreX)} × {scoreLabel(scoreY)}</span>
              <span className="ml-auto whitespace-nowrap">Click point to view pose · Drag to zoom · Double-click to reset</span>
            </div>
            <ScatterPlot
              points={plottedPoints}
              xLabel={scoreLabel(scoreX)}
              yLabel={scoreLabel(scoreY)}
              legend={colorLegend}
              selectedChemotypes={selectedChemotypes}
              selectedPointKey={selectedPoseKey}
              onSelectPoint={(point) => {
                setSelectedPoseKey(point.source.key);
                onSelectPose(point.source);
              }}
            />
          </section>
        </div>
      )}
    </div>
  );
}

function Control({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="min-w-0 text-[10px] font-semibold uppercase tracking-wider text-slate-500"><span className="mb-1 block">{label}</span>{children}</label>;
}

function MetricOptions({
  columns,
  includeIfpCluster = false,
}: {
  columns: string[];
  includeIfpCluster?: boolean;
}) {
  const properties = columns.filter((column) => chemicalPropertyKeys.has(column));
  const ifpMetrics = columns.filter((column) => ifpMetricKeys.has(column));
  const scores = columns.filter(
    (column) => !chemicalPropertyKeys.has(column) && !ifpMetricKeys.has(column),
  );
  return (
    <>
      {scores.length > 0 && (
        <optgroup label="Scores">
          {scores.map((column) => (
            <option key={column} value={column}>{scoreLabel(column)}</option>
          ))}
        </optgroup>
      )}
      {properties.length > 0 && (
        <optgroup label="Chemical properties">
          {properties.map((column) => (
            <option key={column} value={column}>{scoreLabel(column)}</option>
          ))}
        </optgroup>
      )}
      {(includeIfpCluster || ifpMetrics.length > 0) && (
        <optgroup label="Interaction fingerprints">
          {includeIfpCluster && (
            <option value="ifp_cluster_id">IFP Cluster</option>
          )}
          {ifpMetrics.map((column) => (
            <option key={column} value={column}>{scoreLabel(column)}</option>
          ))}
        </optgroup>
      )}
    </>
  );
}

function ScatterPlot({
  points,
  xLabel,
  yLabel,
  legend,
  selectedChemotypes,
  selectedPointKey,
  onSelectPoint,
}: {
  points: ChartPoint[];
  xLabel: string;
  yLabel: string;
  legend: { key: string; label: string; color: string }[];
  selectedChemotypes: Set<string>;
  selectedPointKey?: string;
  onSelectPoint: (point: ChartPoint) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const plottedRef = useRef<{ x: number; y: number; radius: number; point: ChartPoint }[]>([]);
  const scaleRef = useRef<{
    left: number;
    right: number;
    top: number;
    bottom: number;
    inverseX: (value: number) => number;
    inverseY: (value: number) => number;
  }>();
  const dragStartRef = useRef<{ x: number; y: number }>();
  const [view, setView] = useState<{
    xLow: number;
    xHigh: number;
    yLow: number;
    yHigh: number;
  }>();
  const [dragBox, setDragBox] = useState<{
    startX: number;
    startY: number;
    endX: number;
    endY: number;
  }>();
  const [hover, setHover] = useState<{
    left: number;
    top: number;
    below: boolean;
    point: ChartPoint;
  }>();

  useEffect(() => {
    setView(undefined);
    setDragBox(undefined);
    dragStartRef.current = undefined;
  }, [xLabel, yLabel, points.length]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const container = canvas.parentElement;
    if (!container) return;
    const draw = () => {
      const width = Math.max(480, container.clientWidth);
      const height = Math.max(420, container.clientHeight);
      const ratio = window.devicePixelRatio || 1;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      context.fillStyle = "#091523";
      context.fillRect(0, 0, width, height);
      if (!points.length) {
        context.fillStyle = "#64748b";
        context.font = "13px system-ui";
        context.textAlign = "center";
        context.fillText("No molecules have numeric values for both selected axes.", width / 2, height / 2);
        plottedRef.current = [];
        return;
      }
      const margin = { left: 72, right: 28, top: 24, bottom: 62 };
      const xValues = points.map((point) => point.x);
      const yValues = points.map((point) => point.y);
      const extent = (values: number[]) => {
        let low = Number.POSITIVE_INFINITY;
        let high = Number.NEGATIVE_INFINITY;
        for (const value of values) {
          if (value < low) low = value;
          if (value > high) high = value;
        }
        const padding = (high - low || Math.max(Math.abs(low), 1)) * 0.08;
        low -= padding;
        high += padding;
        return [low, high] as const;
      };
      const [baseXLow, baseXHigh] = extent(xValues);
      const [baseYLow, baseYHigh] = extent(yValues);
      const xLow = view?.xLow ?? baseXLow;
      const xHigh = view?.xHigh ?? baseXHigh;
      const yLow = view?.yLow ?? baseYLow;
      const yHigh = view?.yHigh ?? baseYHigh;
      const xScale = (value: number) => margin.left + ((value - xLow) / (xHigh - xLow)) * (width - margin.left - margin.right);
      const yScale = (value: number) => height - margin.bottom - ((value - yLow) / (yHigh - yLow)) * (height - margin.top - margin.bottom);
      scaleRef.current = {
        left: margin.left,
        right: width - margin.right,
        top: margin.top,
        bottom: height - margin.bottom,
        inverseX: (value) => xLow + ((value - margin.left) / (width - margin.left - margin.right)) * (xHigh - xLow),
        inverseY: (value) => yLow + ((height - margin.bottom - value) / (height - margin.top - margin.bottom)) * (yHigh - yLow),
      };
      context.strokeStyle = "#1d3448";
      context.fillStyle = "#64748b";
      context.lineWidth = 1;
      context.font = "11px ui-monospace, monospace";
      for (let index = 0; index <= 5; index += 1) {
        const fraction = index / 5;
        const x = margin.left + fraction * (width - margin.left - margin.right);
        const y = height - margin.bottom - fraction * (height - margin.top - margin.bottom);
        context.beginPath();
        context.moveTo(x, margin.top);
        context.lineTo(x, height - margin.bottom);
        context.moveTo(margin.left, y);
        context.lineTo(width - margin.right, y);
        context.stroke();
        context.textAlign = "center";
        context.fillText((xLow + fraction * (xHigh - xLow)).toFixed(2), x, height - margin.bottom + 20);
        context.textAlign = "right";
        context.fillText((yLow + fraction * (yHigh - yLow)).toFixed(2), margin.left - 9, y + 4);
      }
      context.fillStyle = "#cbd5e1";
      context.font = "12px system-ui";
      context.textAlign = "center";
      context.fillText(xLabel, margin.left + (width - margin.left - margin.right) / 2, height - 16);
      context.save();
      context.translate(18, margin.top + (height - margin.top - margin.bottom) / 2);
      context.rotate(-Math.PI / 2);
      context.fillText(yLabel, 0, 0);
      context.restore();

      const sizeValues = points.map((point) => point.size).filter((value): value is number => value !== undefined).sort((a, b) => a - b);
      const quantile = (fraction: number) => sizeValues[Math.floor((sizeValues.length - 1) * fraction)];
      const sizeLow = sizeValues.length ? quantile(0.05) : 0;
      const sizeHigh = sizeValues.length ? quantile(0.95) : 0;
      const radius = (value?: number) => {
        if (value === undefined || sizeHigh === sizeLow) return 4.5;
        const normalized = Math.max(0, Math.min(1, (value - sizeLow) / (sizeHigh - sizeLow)));
        return 3 + Math.sqrt(normalized) * 9;
      };
      context.save();
      context.beginPath();
      context.rect(
        margin.left,
        margin.top,
        width - margin.left - margin.right,
        height - margin.top - margin.bottom,
      );
      context.clip();
      plottedRef.current = points.flatMap((point) => {
        const x = xScale(point.x);
        const y = yScale(point.y);
        if (
          x < margin.left ||
          x > width - margin.right ||
          y < margin.top ||
          y > height - margin.bottom
        )
          return [];
        const pointRadius = radius(point.size);
        const selectedPoint = point.source.key === selectedPointKey;
        const muted =
          selectedChemotypes.size > 0 &&
          !selectedChemotypes.has(point.chemotypeId);
        context.beginPath();
        context.arc(x, y, pointRadius + (selectedPoint ? 2 : 0), 0, Math.PI * 2);
        context.globalAlpha = muted ? 0.3 : 0.8;
        context.fillStyle = muted ? "#64748b" : point.color;
        context.fill();
        context.globalAlpha = 1;
        context.strokeStyle = selectedPoint
          ? "#f8fafc"
          : muted
            ? "#94a3b833"
            : "#e2e8f055";
        context.lineWidth = selectedPoint ? 2.5 : 1;
        context.stroke();
        return [{ x, y, radius: pointRadius + (selectedPoint ? 2 : 0), point }];
      });
      context.restore();
    };
    const observer = new ResizeObserver(draw);
    observer.observe(container);
    draw();
    return () => observer.disconnect();
  }, [
    points,
    xLabel,
    yLabel,
    selectedChemotypes,
    selectedPointKey,
    view,
  ]);

  const pointerPosition = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return {
      bounds,
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    };
  };

  const nearestPlottedPoint = (x: number, y: number) => {
    let nearest: (typeof plottedRef.current)[number] | undefined;
    let distance = Number.POSITIVE_INFINITY;
    for (const item of plottedRef.current) {
      const current = Math.hypot(item.x - x, item.y - y);
      if (current <= item.radius + 5 && current < distance) {
        nearest = item;
        distance = current;
      }
    }
    return nearest;
  };

  const finishDrag = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const start = dragStartRef.current;
    const scale = scaleRef.current;
    dragStartRef.current = undefined;
    setDragBox(undefined);
    if (!start || !scale) return false;
    const { x, y } = pointerPosition(event);
    const left = Math.max(scale.left, Math.min(start.x, x));
    const right = Math.min(scale.right, Math.max(start.x, x));
    const top = Math.max(scale.top, Math.min(start.y, y));
    const bottom = Math.min(scale.bottom, Math.max(start.y, y));
    if (right - left < 8 || bottom - top < 8) return false;
    setView({
      xLow: scale.inverseX(left),
      xHigh: scale.inverseX(right),
      yLow: scale.inverseY(bottom),
      yHigh: scale.inverseY(top),
    });
    return true;
  };

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden">
      <canvas
        ref={canvasRef}
        className="block h-full w-full cursor-crosshair"
        aria-label="Docking score correlation scatter plot"
        onDoubleClick={() => {
          setView(undefined);
          setHover(undefined);
          setDragBox(undefined);
          dragStartRef.current = undefined;
        }}
        onMouseDown={(event) => {
          if (event.button !== 0) return;
          const { x, y } = pointerPosition(event);
          dragStartRef.current = { x, y };
          setDragBox({ startX: x, startY: y, endX: x, endY: y });
          setHover(undefined);
        }}
        onMouseUp={(event) => {
          const { x, y } = pointerPosition(event);
          if (finishDrag(event)) return;
          const nearest = nearestPlottedPoint(x, y);
          if (nearest) onSelectPoint(nearest.point);
        }}
        onMouseLeave={() => {
          setHover(undefined);
          setDragBox(undefined);
          dragStartRef.current = undefined;
        }}
        onMouseMove={(event) => {
          const { bounds, x, y } = pointerPosition(event);
          if (dragStartRef.current) {
            setDragBox({
              startX: dragStartRef.current.x,
              startY: dragStartRef.current.y,
              endX: x,
              endY: y,
            });
            return;
          }
          const nearest = nearestPlottedPoint(x, y);
          setHover(
            nearest
              ? {
                  left: Math.max(136, Math.min(bounds.width - 136, nearest.x)),
                  top: nearest.y,
                  below: nearest.y < 190,
                  point: nearest.point,
                }
              : undefined,
          );
        }}
      />
      {dragBox && (
        <div
          className="pointer-events-none absolute border border-cyan bg-cyan/10"
          style={{
            left: Math.min(dragBox.startX, dragBox.endX),
            top: Math.min(dragBox.startY, dragBox.endY),
            width: Math.abs(dragBox.endX - dragBox.startX),
            height: Math.abs(dragBox.endY - dragBox.startY),
          }}
        />
      )}
      {legend.length > 0 && (
        <div className="absolute right-4 top-4 z-10 max-h-40 max-w-56 overflow-y-auto rounded-lg border border-line bg-[#07111f]/90 p-2 text-[10px] shadow-lg backdrop-blur-sm">
          {legend.map((item) => (
            <div key={item.key} className="flex items-center gap-2 py-0.5">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: item.color }} />
              <span className="truncate text-slate-300" title={item.label}>{item.label}</span>
            </div>
          ))}
        </div>
      )}
      {hover && (
        <div className={`pointer-events-none absolute z-20 w-64 -translate-x-1/2 rounded-lg border border-line bg-[#07111f]/95 px-3 py-2 text-xs shadow-xl ${hover.below ? "translate-y-3" : "-translate-y-[calc(100%+10px)]"}`} style={{ left: hover.left, top: hover.top }}>
          <div className="font-semibold text-slate-100">{hover.point.ligandName}</div>
          <div className="text-slate-500">{hover.point.ligandCode} · {hover.point.jobName}</div>
          <div className="text-slate-500">{hover.point.chemotypeId}</div>
          {hover.point.source.ifp_cluster_id && (
            <div className="text-slate-500">
              {hover.point.source.ifp_cluster_id} · {hover.point.source.ifp_status}
            </div>
          )}
          {hover.point.smiles && (
            <img
              className="mt-2 h-32 w-full rounded-md bg-white object-contain"
              src={api.structureSvgUrl(hover.point.smiles, 480, 240)}
              alt={`${hover.point.ligandName} structure`}
            />
          )}
          <div className="mt-1 font-mono text-slate-300">x {hover.point.x.toFixed(3)} · y {hover.point.y.toFixed(3)}</div>
        </div>
      )}
    </div>
  );
}
