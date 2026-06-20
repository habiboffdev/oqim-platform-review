import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '@/lib/api-client'
import { uz } from '@/lib/uz'
import type { CustomerDetail, CustomerListResponse } from '@/lib/types'

export function useCustomers() {
  return useQuery({
    queryKey: ['customers'],
    queryFn: () => api.get<CustomerListResponse>('/api/customers'),
    staleTime: 30_000,
  })
}

export function useCustomer(id: number | undefined) {
  return useQuery({
    queryKey: ['customers', id],
    queryFn: () => api.get<CustomerDetail>(`/api/customers/${id}`),
    enabled: !!id,
    staleTime: 30_000,
  })
}

export function useUpdateCustomer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...data }: { id: number; notes?: string; contact_type?: string; ai_muted?: boolean }) =>
      api.patch(`/api/customers/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['customers'] })
      toast.success(uz.common.saved)
    },
    onError: () => { toast.error(uz.common.error) },
  })
}

export function useExportCustomers() {
  return useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/customers/export', {
        credentials: 'include',
      })
      if (!res.ok) throw new Error('Export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `customers-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
    },
    onSuccess: () => { toast.success(uz.customers.export) },
    onError: () => { toast.error(uz.common.error) },
  })
}
