import { HTMLProps, useCallback, useEffect, useRef, useState } from 'react'
import colors from 'tailwindcss/colors'
import { AudioData } from '@/lib/audio-data'
import { useAudioReady } from '@/lib/lyrics-review/hooks/useAudioReady'

export interface WaveformVisualizerProps extends Omit<HTMLProps<HTMLCanvasElement>, 'width' | 'height'> {
	startTime: number
	endTime: number
	audioData: AudioData | null
	barColor?: string
}

export const WaveformVisualizer = ({
	startTime,
	endTime,
	audioData,
	className,
	barColor = colors.yellow['500'],
	... canvasProps
}: WaveformVisualizerProps) => {
	const containerRef = useRef<HTMLDivElement | null>(null)
	const canvasCtxRef = useRef<CanvasRenderingContext2D | null>(null)
	const audioReady = useAudioReady()
	const [canvasSize, setCanvasSize] = useState<{ width: number; height: number; }>({ width: 0, height: 0 })

	useEffect(() => {
		if (!containerRef.current) return

		const resizeObserver = new ResizeObserver((entries) => {
			const size = entries.at(-1)!.contentBoxSize[0]
			setCanvasSize({
				width: size.inlineSize,
				height: size.blockSize
			})
		})
		resizeObserver.observe(containerRef.current, {
			box: 'content-box'
		})

		return () => {
			resizeObserver.disconnect()
		}
	}, [containerRef.current])

	const readAudioData = useCallback((startTime: number, endTime: number) => {
		if (audioData == null) return null

		// Index directly into the time-based peak envelope (peaksPerSecond buckets
		// per second) so we only ever touch the window being drawn, never the whole
		// track.
		const startIndex = Math.max(0, Math.floor(startTime * audioData.peaksPerSecond))
		const endIndex = Math.min(audioData.peaks.length, Math.ceil(endTime * audioData.peaksPerSecond))
		return audioData.peaks.slice(startIndex, endIndex)
	}, [audioData])

	const render = useCallback((startTime: number, endTime: number) => {
		const ctx = canvasCtxRef.current
		if (!ctx) return

		ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height)

		ctx.fillStyle = barColor

		const audioData = readAudioData(startTime, endTime)
		if (!audioData) return

		for (let x = 0; x < ctx.canvas.width; x++) {
			// Clamp the first pixel: (x - 0.5) is negative at x=0, and a negative
			// slice start would sample peaks from the end of the window instead.
			const dataPointIndex = Math.max(0, Math.floor((x - 0.5) / ctx.canvas.width * audioData.length))
			const nextDataPointIndex = Math.floor((x + 0.5) / ctx.canvas.width * audioData.length)

			const amplitude = audioData
				.slice(dataPointIndex, nextDataPointIndex)
				.reduce((maxAmplitude, point) => Math.max(maxAmplitude, Math.abs(point)), 0)

			const barHeight = amplitude * ctx.canvas.height

			ctx.fillRect(x, (ctx.canvas.height - barHeight) / 2, 1, barHeight)
		}
	}, [readAudioData, barColor])

	// Re-render when the data (readAudioData is memoized on audioData), the window,
	// the canvas size, or the colour changes. audioData arrives asynchronously, so
	// omitting `render` here would leave the canvas blank until an unrelated prop
	// changed.
	useEffect(() => {
		render(startTime, endTime)
	}, [render, startTime, endTime, canvasSize, audioReady.ready])

	return (
		<div
			ref={containerRef}
			className={className}
		>
			<canvas
				ref={(el) => {
					canvasCtxRef.current = el?.getContext('2d') ?? null
				}}
				className={"w-[100%] h-[100%]"}
				width={canvasSize.width}
				height={canvasSize.height}
				{... canvasProps}
			></canvas>
		</div>
	)
}
