import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null })
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100dvh',
          padding: 24,
          textAlign: 'center',
          color: '#ccc',
          gap: 16,
        }}>
          <div style={{ fontSize: 40 }}>⚠</div>
          <h2 style={{ color: '#ff4466', margin: 0, fontSize: 18 }}>
            Something went wrong
          </h2>
          <p style={{ fontSize: 13, color: '#888', maxWidth: 300, margin: 0 }}>
            {this.state.error?.message ?? 'Unknown error'}
          </p>
          <button
            onClick={this.handleReload}
            style={{
              padding: '10px 24px',
              borderRadius: 12,
              border: '1px solid rgba(0,212,255,0.3)',
              background: 'rgba(0,212,255,0.08)',
              color: 'var(--cyan, #00d4ff)',
              fontSize: 14,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Reload App
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
