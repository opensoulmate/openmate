"use client"

import { useEffect, useState, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { getApiBaseUrl } from "@/lib/api-client"
import { cn } from "@/lib/utils"
import {
  Search, Loader2, Clock, Filter, X, Zap, BarChart3,
  Globe, FileText, Database, Layers, ChevronDown,
} from "lucide-react"
import { PageLayout } from '@/components/page-layout';

interface SearchResult {
  id: string
  title: string
  source?: string
  snippet?: string
  content?: string
  score?: number
  relevance?: number
  created_at?: string
  timestamp?: string
  type?: string
  [key: string]: unknown
}

interface SearchStats {
  total_results?: number
  search_time_ms?: number
  query?: string
  [key: string]: unknown
}

const sourceIcons: Record<string, typeof Globe> = {
  web: Globe,
  document: FileText,
  knowledge: Database,
  default: Layers,
}

const sourceColors: Record<string, string> = {
  web: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  document: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  knowledge: "bg-violet-500/10 text-violet-400 border-violet-500/20",
  default: "bg-muted/30 text-muted-foreground border-border",
}

export function SearchClient() {
  const { t } = useTranslation()
  const apiBase = getApiBaseUrl()
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SearchResult[]>([])
  const [stats, setStats] = useState<SearchStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [useUnified, setUseUnified] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<string>("all")
  const [showFilters, setShowFilters] = useState(false)

  // Fetch search stats on mount
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch(`${apiBase}/api/search/stats`)
        if (res.ok) setStats(await res.json())
      } catch {}
    }
    fetchStats()
  }, [apiBase])

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return
    setLoading(true)
    setSearched(true)
    const startTime = Date.now()
    try {
      const endpoint = useUnified
        ? `/api/search/unified?q=${encodeURIComponent(query)}`
        : `/api/search/?q=${encodeURIComponent(query)}`
      const res = await fetch(`${apiBase}${endpoint}`)
      if (res.ok) {
        const data = await res.json()
        const items = Array.isArray(data) ? data : data.results || data.items || []
        setResults(items)
        setStats(prev => ({
          ...prev,
          total_results: items.length,
          search_time_ms: Date.now() - startTime,
        }))
      } else {
        setResults([])
      }
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [query, apiBase, useUnified])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") handleSearch()
  }

  // Collect unique sources for filter
  const sources = Array.from(new Set(results.map(r => r.source || r.type || "default")))
  const filtered = sourceFilter === "all"
    ? results
    : results.filter(r => (r.source || r.type || "default") === sourceFilter)

  const getSourceStyle = (source: string) => {
    const key = source.toLowerCase()
    return sourceColors[key] || sourceColors.default
  }

  const getSourceIcon = (source: string) => {
    const key = source.toLowerCase()
    return sourceIcons[key] || sourceIcons.default
  }

  return (
      <PageLayout title="搜索">
        
    <div className="px-3 lg:px-6 py-4 lg:py-6 max-w-6xl mx-auto space-y-4 lg:space-y-6 h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-gradient-to-br from-cyan-500/20 to-blue-500/20">
          <Search className="w-6 h-6 text-cyan-400" />
        </div>
        <div>
          <h1 className="text-xl lg:text-2xl font-bold">{t("search.title") || "Search"}</h1>
          <p className="text-xs lg:text-sm text-muted-foreground">
            {t("search.subtitle") || "Search across all your data sources"}
          </p>
        </div>
      </div>

      {/* Search Input */}
      <div className="max-w-3xl mx-auto space-y-3">
        <div className="relative">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t("search.placeholder") || "Search everything..."}
            className="w-full bg-card border border-border rounded-xl pl-12 pr-28 py-4 text-base outline-none focus:ring-2 focus:ring-cyan-500/30 focus:border-cyan-500/50 transition-all"
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2">
            {query && (
              <button
                onClick={() => { setQuery(""); setResults([]); setSearched(false) }}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            )}
            <button
              onClick={handleSearch}
              disabled={loading || !query.trim()}
              className="px-4 py-2 rounded-lg bg-cyan-500 text-white text-xs lg:text-sm font-medium hover:bg-cyan-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Zap className="w-4 h-4" />
              )}
              {t("search.searchBtn") || "Search"}
            </button>
          </div>
        </div>

        {/* Options row */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs lg:text-sm text-muted-foreground cursor-pointer select-none">
              <input
                type="checkbox"
                checked={useUnified}
                onChange={(e) => setUseUnified(e.target.checked)}
                className="rounded border-border"
              />
              <Globe className="w-3.5 h-3.5" />
              {t("search.unified") || "Unified search"}
            </label>
            {results.length > 0 && (
              <button
                onClick={() => setShowFilters(!showFilters)}
                className={cn(
                  "flex items-center gap-1.5 text-xs lg:text-sm px-2.5 py-1 rounded-lg border transition-colors",
                  showFilters
                    ? "bg-cyan-500/10 text-cyan-400 border-cyan-500/20"
                    : "bg-muted/30 text-muted-foreground border-border hover:bg-muted/50"
                )}
              >
                <Filter className="w-3.5 h-3.5" />
                {t("search.filters") || "Filters"}
                <ChevronDown className={cn("w-3 h-3 transition-transform", showFilters && "rotate-180")} />
              </button>
            )}
          </div>
          {stats?.search_time_ms != null && searched && (
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <BarChart3 className="w-3 h-3" />
                {stats.total_results ?? filtered.length} {t("search.results") || "results"}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {stats.search_time_ms}ms
              </span>
            </div>
          )}
        </div>

        {/* Source filters */}
        {showFilters && sources.length > 1 && (
          <div className="flex flex-wrap gap-2 p-3 bg-card border border-border rounded-lg">
            <button
              onClick={() => setSourceFilter("all")}
              className={cn(
                "px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                sourceFilter === "all"
                  ? "bg-cyan-500/10 text-cyan-400 border-cyan-500/20"
                  : "bg-muted/30 text-muted-foreground border-border hover:bg-muted/50"
              )}
            >
              {t("search.allSources") || "All sources"}
            </button>
            {sources.map(src => {
              const Icon = getSourceIcon(src)
              return (
                <button
                  key={src}
                  onClick={() => setSourceFilter(src)}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
                    sourceFilter === src
                      ? getSourceStyle(src)
                      : "bg-muted/30 text-muted-foreground border-border hover:bg-muted/50"
                  )}
                >
                  <Icon className="w-3 h-3" />
                  {src}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Results */}
      {!searched ? (
        /* Empty state: no search yet */
        <div className="text-center py-20 text-muted-foreground/50">
          <Search className="w-16 h-16 mx-auto mb-4" />
          <p className="text-lg font-medium mb-1">
            {t("search.emptyTitle") || "Search your data"}
          </p>
          <p className="text-xs lg:text-sm">
            {t("search.emptyHint") || "Enter a query to search across knowledge, documents, and more"}
          </p>
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center h-48">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground/50">
          <Search className="w-12 h-12 mx-auto mb-2" />
          <p className="text-xs lg:text-sm">
            {t("search.noResults") || `No results found for "${query}"`}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((result, idx) => {
            const source = result.source || result.type || "unknown"
            const SourceIcon = getSourceIcon(source)
            const score = result.score ?? result.relevance
            return (
              <div
                key={result.id || idx}
                className="bg-card border border-border rounded-xl p-3 lg:p-4 hover:border-border/80 transition-all group"
              >
                <div className="flex items-start gap-3">
                  <div className={cn(
                    "p-2 rounded-lg border shrink-0 mt-0.5",
                    getSourceStyle(source)
                  )}>
                    <SourceIcon className="w-4 h-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h4 className="font-semibold text-xs lg:text-sm">{result.title || result.id}</h4>
                      <span className={cn(
                        "px-1.5 py-0.5 rounded text-[10px] font-medium border",
                        getSourceStyle(source)
                      )}>
                        {source}
                      </span>
                      {score != null && (
                        <span className="ml-auto text-xs text-muted-foreground flex items-center gap-1 shrink-0">
                          <BarChart3 className="w-3 h-3" />
                          {(typeof score === "number" ? score * 100 : parseFloat(String(score)) * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                    {(result.snippet || result.content) && (
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                        {result.snippet || result.content}
                      </p>
                    )}
                    {(result.created_at || result.timestamp) && (
                      <div className="flex items-center gap-1 mt-2 text-xs text-muted-foreground">
                        <Clock className="w-3 h-3" />
                        {new Date(result.created_at || result.timestamp as string).toLocaleDateString()}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  
      </PageLayout>
    )
}
