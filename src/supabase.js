import { createClient } from '@supabase/supabase-js'

// window.location.origin automatically grabs your Vercel URL (https://gas-monitor-theta.vercel.app)
const supabaseUrl = import.meta.env.PROD
    ? window.location.origin + '/supabase-api'
    : import.meta.env.VITE_SUPABASE_URL;

const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseKey);