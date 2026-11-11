import { describe, expect, it } from "vitest";
import type { DockingAnalysisPoint } from "../types";
import {
  pointMatchesPlotFilters,
  type PlotFilterCondition,
} from "./analysisFilters";

const point = {
  key: "job:ligand:1",
  rank: 1,
  job_id: "job",
  job_name: "Docking job",
  engine: "smina",
  created_at: "2026-11-09T00:00:00Z",
  protein_id: "protein",
  receptor_name: "Receptor",
  match_key: "ligand",
  ligand_id: "ligand",
  ligand_code: "LIG-1",
  ligand_name: "Ligand 1",
  smiles: "CCO",
  pose: 1,
  affinity: -9.2,
  chemotype_id: "CT-1",
  chemotype_smiles: "CCO",
  ifp_cluster_id: "job:IFP-0002",
  ifp_status: "Completed",
  scores: {
    affinity: -9.2,
    MW: 342.4,
    XLogP: 3.1,
    HBD: 1,
    TPSA: 71.2,
  },
  pose_url: "/pose.sdf",
} satisfies DockingAnalysisPoint;

const condition = (
  id: number,
  metric: string,
  minimum = "",
  maximum = "",
  category = "",
): PlotFilterCondition => ({ id, metric, minimum, maximum, category });

describe("pointMatchesPlotFilters", () => {
  it("combines multiple score and chemical-property filters with AND", () => {
    expect(
      pointMatchesPlotFilters(point, [
        condition(1, "affinity", "-10", "-8"),
        condition(2, "MW", "300", "400"),
        condition(3, "HBD", "1", "1"),
      ]),
    ).toBe(true);
    expect(
      pointMatchesPlotFilters(point, [
        condition(1, "affinity", "-10", "-8"),
        condition(2, "TPSA", "", "60"),
      ]),
    ).toBe(false);
  });

  it("filters on a property independently of the plot axis selections", () => {
    expect(
      pointMatchesPlotFilters(point, [condition(1, "XLogP", "3", "4")]),
    ).toBe(true);
    expect(
      pointMatchesPlotFilters(point, [condition(1, "XLogP", "", "2")]),
    ).toBe(false);
  });

  it("supports IFP cluster together with numeric conditions", () => {
    expect(
      pointMatchesPlotFilters(point, [
        condition(1, "ifp_cluster_id", "", "", "job:IFP-0002"),
        condition(2, "MW", "300"),
      ]),
    ).toBe(true);
    expect(
      pointMatchesPlotFilters(point, [
        condition(1, "ifp_cluster_id", "", "", "job:IFP-0001"),
        condition(2, "MW", "300"),
      ]),
    ).toBe(false);
  });

  it("ignores blank conditions and rejects missing active metrics", () => {
    expect(pointMatchesPlotFilters(point, [condition(1, "RoB")])).toBe(true);
    expect(
      pointMatchesPlotFilters(point, [condition(1, "RoB", "1")]),
    ).toBe(false);
  });
});
