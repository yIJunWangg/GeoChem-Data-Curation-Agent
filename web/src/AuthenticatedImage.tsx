import { useEffect, useState, type ImgHTMLAttributes } from 'react'
import { apiAuthorizationHeaders } from './api'

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> & {
  src: string
  onLoadError?: () => void
}

export function AuthenticatedImage({ src, onLoadError, ...props }: Props) {
  const [objectUrl, setObjectUrl] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let localUrl = ''
    setObjectUrl('')
    void fetch(src, {
      headers: apiAuthorizationHeaders(),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
        return response.blob()
      })
      .then((blob) => {
        if (controller.signal.aborted) return
        localUrl = URL.createObjectURL(blob)
        setObjectUrl(localUrl)
      })
      .catch(() => {
        if (!controller.signal.aborted) onLoadError?.()
      })
    return () => {
      controller.abort()
      if (localUrl) URL.revokeObjectURL(localUrl)
    }
  }, [onLoadError, src])

  if (!objectUrl) return null
  return <img {...props} src={objectUrl} />
}
