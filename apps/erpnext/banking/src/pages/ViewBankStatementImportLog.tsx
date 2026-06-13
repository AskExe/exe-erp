import { lazy } from 'react'
import { useGetStatementDetails } from '@/components/features/BankStatementImporter/import_utils'
import { Button } from '@/components/ui/button'
import { useDirection } from '@/components/ui/direction'
import ErrorBanner from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import _ from '@/lib/translate'
import { useFrappeDocumentEventListener } from 'frappe-react-sdk'
import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { Link, useParams } from 'react-router'

const CSVImport = lazy(() => import('@/components/features/BankStatementImporter/CSV/CSVImport'))

const ViewBankStatementImportLog = () => {

    const { id } = useParams<{ id: string }>()

    const { data, isLoading, error } = useGetStatementDetails(id ?? "")

    useFrappeDocumentEventListener("Bank Statement Import Log", id ?? "", () => {
    })

    const direction = useDirection()

    // Check loading first — before checking data — so we never flash an empty
    // state while the fetch is still in flight.
    if (isLoading) {
        return (
            <div className='flex flex-col gap-4 px-4 py-4'>
                <Skeleton className='h-6 w-48' />
                <Skeleton className='h-4 w-full' />
                <Skeleton className='h-4 w-3/4' />
                <Skeleton className='h-64 w-full' />
            </div>
        )
    }

    if (error) {
        return <div className='flex flex-col gap-4 px-4'>
            <div>
                <Button size='sm' variant='outline' asChild>
                    <Link to="/statement-importer">
                        {direction === 'ltr' ? <ChevronLeftIcon /> : <ChevronRightIcon />}
                        {_("Back")}
                    </Link>
                </Button>
            </div>
            <ErrorBanner error={error} />
        </div>
    }

    if (!data || !data.message) {
        return null
    }

    return <CSVImport data={data} />
}

export default ViewBankStatementImportLog