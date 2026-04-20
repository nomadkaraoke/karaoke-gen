'use client'

import React from 'react'
import CrashReport from './CrashReport'

interface Props {
  children: React.ReactNode
  source: string
  backHref?: string
}

interface State {
  error: Error | null
  key: number
}

export default class CrashReportBoundary extends React.Component<Props, State> {
  state: State = { error: null, key: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    if (typeof console !== 'undefined') {
      console.error('[CrashReportBoundary]', error, info.componentStack)
    }
  }

  reset = () => {
    this.setState((s) => ({ error: null, key: s.key + 1 }))
  }

  render() {
    if (this.state.error) {
      return (
        <CrashReport
          error={this.state.error}
          source={this.props.source}
          onReset={this.reset}
          backHref={this.props.backHref}
        />
      )
    }
    return <React.Fragment key={this.state.key}>{this.props.children}</React.Fragment>
  }
}
