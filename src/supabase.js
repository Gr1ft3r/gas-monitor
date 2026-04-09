import { createClient } from '@supabase/supabase-js'

// Using a relative path makes the browser handle the domain automatically
const supabaseUrl = import.meta.env.PROD 
  ? '/supabase-api' 
  : import.meta.env.VITE_SUPABASE_URL;

const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase = createClient(supabaseUrl, supabaseKey);