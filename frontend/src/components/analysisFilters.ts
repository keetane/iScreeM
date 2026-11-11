import type { DockingAnalysisPoint } from "../types";

export type PlotFilterCondition = {
  id: number;
  metric: string;
  minimum: string;
  maximum: string;
  category: string;
};

const finiteNumber = (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

export const pointMatchesPlotFilters = (
  point: DockingAnalysisPoint,
  filters: PlotFilterCondition[],
) =>
  filters.every((filter) => {
    if (filter.metric === "ifp_cluster_id")
      return !filter.category || point.ifp_cluster_id === filter.category;

    const hasMinimum = filter.minimum.trim() !== "";
    const hasMaximum = filter.maximum.trim() !== "";
    if (!hasMinimum && !hasMaximum) return true;

    const value = finiteNumber(point.scores[filter.metric]);
    if (value === undefined) return false;
    const minimum = hasMinimum ? Number(filter.minimum) : undefined;
    const maximum = hasMaximum ? Number(filter.maximum) : undefined;
    return (
      (minimum === undefined || !Number.isFinite(minimum) || value >= minimum) &&
      (maximum === undefined || !Number.isFinite(maximum) || value <= maximum)
    );
  });
