"use client"

import Link from "next/link"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { LucideIcon } from "lucide-react"

interface StatsCardProps {
  title: string
  value: string | number
  description?: string
  icon?: LucideIcon
  trend?: {
    value: number
    label: string
    positive?: boolean
  }
  loading?: boolean
  className?: string
  valueClassName?: string
  /** When set, the whole card becomes a link to this page */
  href?: string
}

export function StatsCard({
  title,
  value,
  description,
  icon: Icon,
  trend,
  loading,
  className,
  valueClassName,
  href,
}: StatsCardProps) {
  if (loading) {
    return (
      <Card className={cn("", className)}>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">
            <Skeleton className="h-4 w-24" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-8 w-16" />
          <Skeleton className="h-3 w-32 mt-2" />
        </CardContent>
      </Card>
    )
  }

  const card = (
    <Card className={cn(href && "transition-colors hover:bg-muted/50 hover:border-primary/30", className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        {Icon && <Icon className="h-4 w-4 text-muted-foreground" />}
      </CardHeader>
      <CardContent>
        <div className={cn("text-2xl font-bold", valueClassName)}>{value}</div>
        {description && (
          <p className="text-xs text-muted-foreground mt-1">{description}</p>
        )}
        {trend && (
          <p className={cn(
            "text-xs mt-1",
            trend.positive === true ? "text-green-500" : trend.positive === false ? "text-red-500" : "text-muted-foreground"
          )}>
            {trend.positive === true ? "+" : ""}{trend.value}% {trend.label}
          </p>
        )}
      </CardContent>
    </Card>
  )

  if (href) {
    return <Link href={href} className="block">{card}</Link>
  }
  return card
}

interface StatsGridProps {
  children: React.ReactNode
  className?: string
  columns?: 2 | 3 | 4
}

export function StatsGrid({ children, className, columns }: StatsGridProps) {
  const colsClass = columns === 2
    ? "md:grid-cols-2"
    : columns === 3
    ? "md:grid-cols-2 lg:grid-cols-3"
    : "md:grid-cols-2 lg:grid-cols-4"

  return (
    <div className={cn(
      "grid gap-4",
      colsClass,
      className
    )}>
      {children}
    </div>
  )
}
