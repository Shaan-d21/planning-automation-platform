import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { OperationExecution } from "../api/types";

export function useOperationMonitor(executionId: string | null) {
  const [execution, setExecution] = useState<OperationExecution | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setExecution(null);
    setError(null);
    if (!executionId) return;
    let active = true;
    let timer: number | undefined;

    const inspect = async () => {
      try {
        const result = await api.operationRun(executionId);
        if (!active) return;
        setExecution(result);
        setError(null);
        if (!result.terminal) timer = window.setTimeout(inspect, 2500);
      } catch (reason) {
        if (!active) return;
        setError(reason instanceof Error ? reason.message : "Execution status is temporarily unavailable.");
        timer = window.setTimeout(inspect, 5000);
      }
    };

    void inspect();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [executionId]);

  return { execution, monitorError: error };
}
