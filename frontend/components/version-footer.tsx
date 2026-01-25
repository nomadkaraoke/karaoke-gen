"use client";

import { useEffect, useState } from "react";
import { api, EncodingWorkerHealth, FlacfetchHealth } from "@/lib/api";

export function VersionFooter() {
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  const [encodingWorker, setEncodingWorker] = useState<EncodingWorkerHealth | null>(null);
  const [flacfetch, setFlacfetch] = useState<FlacfetchHealth | null>(null);
  // Frontend version is injected at build time from pyproject.toml
  const frontendVersion = process.env.NEXT_PUBLIC_APP_VERSION || "dev";

  useEffect(() => {
    const fetchBackendVersion = async () => {
      try {
        const info = await api.getBackendInfo();
        setBackendVersion(info.version);
      } catch (error) {
        console.error("Failed to fetch backend version:", error);
        setBackendVersion(null);
      }
    };

    const fetchEncodingWorkerHealth = async () => {
      try {
        const health = await api.getEncodingWorkerHealth();
        setEncodingWorker(health);
      } catch (error) {
        console.error("Failed to fetch encoding worker health:", error);
        setEncodingWorker(null);
      }
    };

    const fetchFlacfetchHealth = async () => {
      try {
        const health = await api.getFlacfetchHealth();
        setFlacfetch(health);
      } catch (error) {
        console.error("Failed to fetch flacfetch health:", error);
        setFlacfetch(null);
      }
    };

    fetchBackendVersion();
    fetchEncodingWorkerHealth();
    fetchFlacfetchHealth();
  }, []);

  // Render encoding worker status indicator
  const renderEncodingWorkerStatus = () => {
    if (!encodingWorker) {
      return null;
    }

    if (encodingWorker.status === "not_configured") {
      return null; // Don't show if not configured
    }

    const isHealthy = encodingWorker.available && encodingWorker.status === "ok";
    const statusColor = isHealthy ? "text-green-500" : "text-red-500";
    const statusDot = isHealthy ? "●" : "○";

    // Build status text
    let statusText = isHealthy ? "Encoder" : "Encoder offline";
    if (isHealthy && encodingWorker.version) {
      statusText = `Encoder: v${encodingWorker.version}`;
    }

    // Add active jobs indicator if there are any
    if (isHealthy && encodingWorker.active_jobs && encodingWorker.active_jobs > 0) {
      statusText += ` (${encodingWorker.active_jobs} active)`;
    }

    return (
      <>
        {" | "}
        <span className={statusColor} title={isHealthy ? "Encoding worker healthy" : encodingWorker.error || "Encoding worker offline"}>
          {statusDot}
        </span>{" "}
        {statusText}
      </>
    );
  };

  // Render flacfetch status indicator
  const renderFlacfetchStatus = () => {
    if (!flacfetch) {
      return null;
    }

    if (flacfetch.status === "not_configured") {
      return null; // Don't show if not configured
    }

    const isHealthy = flacfetch.available && flacfetch.status === "ok";
    const statusColor = isHealthy ? "text-green-500" : "text-red-500";
    const statusDot = isHealthy ? "●" : "○";

    // Build status text
    let statusText = isHealthy ? "Flacfetch" : "Flacfetch offline";
    if (isHealthy && flacfetch.version) {
      statusText = `Flacfetch: v${flacfetch.version}`;
    }

    return (
      <>
        {" | "}
        <span className={statusColor} title={isHealthy ? "Flacfetch service healthy" : flacfetch.error || "Flacfetch service offline"}>
          {statusDot}
        </span>{" "}
        {statusText}
      </>
    );
  };

  return (
    <footer className="border-t mt-12 py-6" style={{ borderColor: 'var(--card-border)' }}>
      <div className="px-4 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
        <p>
          Powered by{" "}
          <a href="https://github.com/nomadkaraoke/karaoke-gen" className="text-amber-500 hover:underline">
            karaoke-gen
          </a>
        </p>
        <p className="mt-1 text-xs opacity-75">
          Frontend: v{frontendVersion}
          {backendVersion && (
            <>
              {" | "}
              Backend: v{backendVersion}
            </>
          )}
          {renderEncodingWorkerStatus()}
          {renderFlacfetchStatus()}
        </p>
      </div>
    </footer>
  );
}
