"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, SystemStatus, ServiceStatus } from "@/lib/api";

interface SystemStatusModalProps {
  open: boolean;
  onClose: () => void;
}

function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return "";
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + "\u2026";
}

function StatusDot({ status }: { status: string }) {
  const isOk = status === "ok";
  return (
    <span className={isOk ? "text-green-500" : "text-red-500"}>
      {isOk ? "\u25CF" : "\u25CB"}
    </span>
  );
}

function ServiceCard({
  name,
  service,
  children,
}: {
  name: string;
  service: ServiceStatus;
  children?: React.ReactNode;
}) {
  const isOk = service.status === "ok";
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        borderColor: "var(--card-border)",
        backgroundColor: "var(--card-bg)",
      }}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <StatusDot status={service.status} />
          <span className="font-medium text-sm">{name}</span>
        </div>
        <span className="text-xs opacity-60">
          {isOk ? "Healthy" : "Offline"}
        </span>
      </div>

      {service.version && (
        <div className="text-xs opacity-75 mt-1">v{service.version}</div>
      )}

      {service.deployed_at && (
        <div className="text-xs opacity-50 mt-0.5">
          Deployed {formatRelativeTime(service.deployed_at)}
        </div>
      )}

      {service.pr_number && (
        <div className="text-xs mt-1">
          <a
            href={`https://github.com/nomadkaraoke/karaoke-gen/pull/${service.pr_number}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-amber-500 hover:underline"
          >
            #{service.pr_number}
          </a>
          {service.pr_title && (
            <span className="opacity-60">
              {" \u2014 "}
              {truncate(service.pr_title, 50)}
            </span>
          )}
        </div>
      )}

      {children}
    </div>
  );
}

function BlueGreenSection({ details }: { details: NonNullable<ServiceStatus["admin_details"]> }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-amber-500 hover:text-amber-400 flex items-center gap-1"
      >
        <span>{expanded ? "\u25BE" : "\u25B8"}</span>
        Blue-Green Deployment
        {details.deploy_in_progress && (
          <span className="text-amber-400 animate-pulse ml-1">\u26A0 deploying...</span>
        )}
      </button>

      {expanded && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {/* Primary */}
          <div
            className="rounded p-2 text-xs border"
            style={{
              borderColor: "rgba(74,222,128,0.3)",
              backgroundColor: "rgba(74,222,128,0.05)",
            }}
          >
            <div className="text-green-500 text-[10px] font-semibold uppercase tracking-wider">
              Primary
            </div>
            <div className="mt-1 font-mono">{details.primary_vm}</div>
            <div className="opacity-60">v{details.primary_version}</div>
            {details.primary_deployed_at && (
              <div className="opacity-40">
                {formatRelativeTime(details.primary_deployed_at)}
              </div>
            )}
          </div>

          {/* Secondary */}
          <div
            className="rounded p-2 text-xs border"
            style={{
              borderColor: "var(--card-border)",
              backgroundColor: "rgba(255,255,255,0.02)",
            }}
          >
            <div className="text-[10px] font-semibold uppercase tracking-wider opacity-50">
              Secondary (stopped)
            </div>
            <div className="mt-1 font-mono">{details.secondary_vm}</div>
            {details.secondary_version && (
              <div className="opacity-60">v{details.secondary_version}</div>
            )}
          </div>

          {/* Metadata row */}
          <div className="col-span-2 text-[10px] opacity-40 mt-1">
            {details.last_swap_at && (
              <span>Last swap: {formatRelativeTime(details.last_swap_at)}</span>
            )}
            {details.active_jobs != null && details.active_jobs > 0 && (
              <span className="ml-3">Active jobs: {details.active_jobs}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function SystemStatusModal({ open, onClose }: SystemStatusModalProps) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;

    setLoading(true);
    setError(null);

    api
      .getSystemStatus()
      .then(setStatus)
      .catch((e) => setError(e.message || "Failed to fetch status"))
      .finally(() => setLoading(false));
  }, [open]);

  const frontendVersion = process.env.NEXT_PUBLIC_APP_VERSION || "dev";
  const frontendCommitSha = process.env.NEXT_PUBLIC_COMMIT_SHA || "";
  const frontendPrNumber = process.env.NEXT_PUBLIC_PR_NUMBER || "";
  const frontendPrTitle = process.env.NEXT_PUBLIC_PR_TITLE || "";
  const frontendBuildTime = process.env.NEXT_PUBLIC_BUILD_TIME || "";

  // Override frontend service with build-time values
  const services = status?.services;
  const frontendService: ServiceStatus = {
    status: "ok",
    version: frontendVersion,
    deployed_at: frontendBuildTime || undefined,
    commit_sha: frontendCommitSha || undefined,
    pr_number: frontendPrNumber || undefined,
    pr_title: frontendPrTitle || undefined,
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-lg" style={{ backgroundColor: "var(--card-bg)", borderColor: "var(--card-border)" }}>
        <DialogHeader>
          <DialogTitle className="text-foreground">System Status</DialogTitle>
        </DialogHeader>

        {loading && (
          <div className="py-8 text-center text-sm opacity-60">Loading...</div>
        )}

        {error && (
          <div className="py-8 text-center text-sm text-red-500">{error}</div>
        )}

        {services && !loading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2">
            <ServiceCard name="Frontend" service={frontendService} />
            <ServiceCard name="Backend" service={services.backend} />
            <ServiceCard name="Encoder" service={services.encoder}>
              {services.encoder.admin_details &&
                services.encoder.admin_details.primary_vm && (
                  <BlueGreenSection details={services.encoder.admin_details} />
                )}
            </ServiceCard>
            <ServiceCard name="Flacfetch" service={services.flacfetch} />
            <ServiceCard name="Separator" service={services.separator} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
