import { supabase } from './supabase';
import React, { useState, useEffect, useRef } from 'react';

export default function App() {
  const longPressTimer = useRef(null);
  const deleteHoldTimer = useRef(null);

  // --- STATE VARIABLES ---
  const [rawPrices, setRawPrices] = useState([]);
  const [selectedBrand, setSelectedBrand] = useState('Dashboard');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedStationId, setExpandedStationId] = useState(null);
  const [expandedDashboardKey, setExpandedDashboardKey] = useState(null); // "City|Brand"
  const [dbError, setDbError] = useState(null);
  const [updateModal, setUpdateModal] = useState({ isOpen: false, priceId: null, currentPrice: '', stationId: null, stationName: '', fuelName: '' });
  const [newPriceInput, setNewPriceInput] = useState('');
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', isError: false });
  const [customFuelType, setCustomFuelType] = useState('');
  const [deleteModal, setDeleteModal] = useState({ isOpen: false, stationId: null, stationName: '', priceId: null, fuelType: '', mode: 'fuel' });
  const [deletePinInput, setDeletePinInput] = useState('');

  // Form State
  const [stationBrand, setStationBrand] = useState('Petron');
  const [branchName, setBranchName] = useState('');
  const [cityName, setCityName] = useState('Baguio City');
  const [fuelType, setFuelType] = useState('');
  const [price, setPrice] = useState('');

  useEffect(() => { fetchPrices(); }, []);

  async function fetchPrices() {
    const { data, error } = await supabase
      .from('prices')
      .select('*, stations!inner(id, name, status, city)')
      .neq('status', 'Archived')
      .neq('status', 'Pending_Admin')
      .neq('stations.status', 'Pending_Admin');
    if (error) setDbError(error.message);
    else { setRawPrices(data); setDbError(null); }
  }

  async function generateFingerprint() {
    const components = [
      navigator.userAgent,
      navigator.language,
      navigator.languages?.join(','),
      screen.width + 'x' + screen.height + 'x' + screen.colorDepth,
      new Date().getTimezoneOffset(),
      navigator.hardwareConcurrency || 'unknown',
      navigator.platform,
      navigator.deviceMemory || 'unknown',
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    ].join('|||');
    const msgBuffer = new TextEncoder().encode(components);
    const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return 'fp_' + hashArray.map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 20);
  }

  async function getDeviceId() {
    const fingerprint = await generateFingerprint();
    try {
      let storedId = localStorage.getItem('gas_monitor_device_id');
      if (!storedId) {
        localStorage.setItem('gas_monitor_device_id', fingerprint);
        return fingerprint;
      }
      return storedId;
    } catch (e) {
      return fingerprint;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // ADMIN DELETE: Long-press "Not Sold" → PIN prompt → archive
  // ─────────────────────────────────────────────────────────────
  async function handleAdminDelete() {
    const { stationId, priceId, fuelType: deleteFuelType, mode } = deleteModal;
    const SECRET_PIN = '6949';

    if (deletePinInput.trim() !== SECRET_PIN) {
      setDeletePinInput('');
      setAlertModal({ isOpen: true, title: '❌ Wrong PIN', message: 'Incorrect PIN. Delete cancelled.', isError: true });
      setDeleteModal({ isOpen: false, stationId: null, stationName: '', priceId: null, fuelType: '', mode: 'fuel' });
      return;
    }

    if (mode === 'fuel') {
      await supabase.from('prices').update({ status: 'Archived' }).eq('id', priceId);
      setAlertModal({ isOpen: true, title: '🗑️ Fuel Removed', message: `${deleteFuelType} has been permanently removed from this station.`, isError: false });
    } else {
      await supabase.from('prices').update({ status: 'Archived' }).eq('station_id', stationId).neq('status', 'Archived');
      await supabase.from('stations').update({ status: 'Archived' }).eq('id', stationId);
      setAlertModal({ isOpen: true, title: '🗑️ Station Deleted', message: 'Station has been permanently removed.', isError: false });
    }

    setDeleteModal({ isOpen: false, stationId: null, stationName: '', priceId: null, fuelType: '', mode: 'fuel' });
    setDeletePinInput('');
    fetchPrices();
  }

  // ─────────────────────────────────────────────────────────────
  // CASCADE PRICING
  // ─────────────────────────────────────────────────────────────
  async function cascadePrice(stationId, fuelTypeName, newPrice, oldPrice, asVerified) {
    if (fuelTypeName.toLowerCase().includes('kerosene')) return;

    const { data: originStation } = await supabase
      .from('stations').select('id, name, city').eq('id', stationId).single();
    if (!originStation) return;

    const brandKeywords = ['Petron', 'Shell', 'Caltex', 'Cleanfuel', 'Phoenix', 'Seaoil', 'Flying V', 'Total'];
    const matchedBrand = brandKeywords.find(b => originStation.name.toLowerCase().includes(b.toLowerCase()));
    if (!matchedBrand) return;

    const { data: siblingStations } = await supabase
      .from('stations').select('id, name')
      .ilike('name', `%${matchedBrand}%`)
      .eq('city', originStation.city)
      .neq('id', stationId);

    if (!siblingStations || siblingStations.length === 0) return;

    const cascadeStatus = asVerified ? 'Verified' : 'Unverified';
    const cascadeUpvotes = asVerified ? 3 : 1;
    let cascadeCount = 0;

    for (const sibling of siblingStations) {
      const { data: siblingCurrentRows } = await supabase
        .from('prices').select('id, price')
        .eq('station_id', sibling.id)
        .eq('fuel_type', fuelTypeName)
        .neq('status', 'Archived')
        .order('id', { ascending: false })
        .limit(1);

      const siblingOldPrice = siblingCurrentRows && siblingCurrentRows.length > 0
        ? siblingCurrentRows[0].price
        : oldPrice;

      await supabase.from('prices').update({ status: 'Archived' })
        .eq('station_id', sibling.id)
        .eq('fuel_type', fuelTypeName)
        .neq('status', 'Archived');

      const { error } = await supabase.from('prices').insert([{
        station_id: sibling.id,
        fuel_type: fuelTypeName,
        price: newPrice,
        old_price: siblingOldPrice,
        status: cascadeStatus,
        upvotes: cascadeUpvotes,
      }]);

      if (!error) cascadeCount++;
    }

    if (cascadeCount > 0) {
      console.log(`[CASCADE] ${fuelTypeName} @ ${matchedBrand}/${originStation.city}: updated ${cascadeCount} sibling station(s) as ${cascadeStatus}`);
    }
  }

  async function handleUpvotePrice(priceId, currentUpvotes, stationId, fuelTypeName) {
    const deviceId = await getDeviceId();
    const { error: logError } = await supabase.from('user_votes').insert([{ price_id: priceId, device_id: deviceId, vote_type: 'upvote' }]);
    if (logError && logError.code === '23505') {
      setAlertModal({ isOpen: true, title: "Anti-Spam", message: "You have already verified this price!", isError: true });
      return;
    }

    const newUpvoteCount = currentUpvotes + 1;
    if (newUpvoteCount >= 3) {
      await supabase.from('prices').update({ status: 'Archived' }).eq('station_id', stationId).eq('fuel_type', fuelTypeName).eq('status', 'Verified');
      const { data: verifiedRows } = await supabase.from('prices').update({ upvotes: newUpvoteCount, status: 'Verified' }).eq('id', priceId).select();
      await supabase.from('stations').update({ status: 'Active' }).eq('id', stationId);
      if (verifiedRows && verifiedRows.length > 0) {
        await cascadePrice(stationId, fuelTypeName, verifiedRows[0].price, verifiedRows[0].old_price, false);
      }
    } else {
      await supabase.from('prices').update({ upvotes: newUpvoteCount }).eq('id', priceId);
    }
    fetchPrices();
  }

  async function submitPriceUpdate(e) {
    e.preventDefault();
    const { priceId, currentPrice, stationId, fuelName } = updateModal;
    const rawInput = newPriceInput.trim();
    if (!rawInput) return;

    const isSuperAdmin = rawInput.endsWith('*');
    const newPrice = parseFloat(rawInput.replace('*', ''));

    if (!isNaN(newPrice) && newPrice > 30 && newPrice < 250) {
      await supabase.from('prices').update({ status: 'Archived' }).eq('id', priceId);
      await supabase.from('prices').insert([{
        station_id: stationId, fuel_type: fuelName, price: newPrice, old_price: currentPrice,
        status: isSuperAdmin ? 'Verified' : 'Unverified', upvotes: isSuperAdmin ? 3 : 1
      }]);
      if (isSuperAdmin) {
        await supabase.from('stations').update({ status: 'Active' }).eq('id', stationId).eq('status', 'Unverified');
        await cascadePrice(stationId, fuelName, newPrice, currentPrice, true);
      }
      fetchPrices();
      setUpdateModal({ isOpen: false });
      setNewPriceInput('');
      setAlertModal({
        isOpen: true,
        title: isSuperAdmin ? "✅ Verified" : "Update Submitted",
        message: isSuperAdmin
          ? "Price instantly verified! Station has also been marked Active."
          : "Update submitted! Awaiting community verification.",
        isError: false
      });
    } else {
      setAlertModal({ isOpen: true, title: "Invalid Input", message: "Please enter a realistic fuel price.", isError: true });
    }
  }

  async function handleOutOfStock(priceId, currentVotes) {
    const deviceId = await getDeviceId();
    const { error: logError } = await supabase.from('user_votes').insert([{ price_id: priceId, device_id: deviceId, vote_type: 'out_of_stock' }]);
    if (logError && logError.code === '23505') {
      setAlertModal({ isOpen: true, title: "Anti-Spam", message: "You already reported this as Out of Stock!", isError: true });
      return;
    }
    const votes = currentVotes ? currentVotes : 0;
    await supabase.from('prices').update({ out_of_stock_votes: votes + 1 }).eq('id', priceId);
    fetchPrices();
    setAlertModal({ isOpen: true, title: "Report Received", message: "Report received! If 2 more people confirm this, it will be marked Out of Stock.", isError: false });
  }

  async function handleRetiredFuel(priceId, currentVotes) {
    const deviceId = await getDeviceId();
    const { error: logError } = await supabase.from('user_votes').insert([{ price_id: priceId, device_id: deviceId, vote_type: 'retired' }]);
    if (logError && logError.code === '23505') {
      setAlertModal({ isOpen: true, title: "Anti-Spam", message: "You already reported this fuel as not sold!", isError: true });
      return;
    }
    const votes = (currentVotes ? currentVotes : 0) + 1;
    if (votes >= 3) {
      await supabase.from('prices').update({ status: 'Archived', retired_votes: votes }).eq('id', priceId);
      setAlertModal({ isOpen: true, title: "Fuel Removed", message: "Fuel removed! 3 users have confirmed this station no longer sells it.", isError: false });
    } else {
      await supabase.from('prices').update({ retired_votes: votes }).eq('id', priceId);
      setAlertModal({ isOpen: true, title: "Report Received", message: `Report received! (${votes}/3) — ${3 - votes} more needed to remove this fuel.`, isError: false });
    }
    fetchPrices();
  }

  async function handleAddStation(e) {
    e.preventDefault();
    const resolvedFuelType = fuelType === 'Other' ? customFuelType.trim() : fuelType;
    if (!resolvedFuelType) {
      setAlertModal({ isOpen: true, title: "Missing Fuel Type", message: "Please specify the fuel type in the text field.", isError: true });
      return;
    }
    const fullStationName = `${stationBrand} - ${branchName}`;
    const newPrice = parseFloat(price.replace('*', ''));
    if (isNaN(newPrice) || newPrice < 30 || newPrice > 250) {
      setAlertModal({ isOpen: true, title: "Invalid Input", message: "Please enter a realistic fuel price between ₱30 and ₱250.", isError: true });
      return;
    }
    const { data: existingStation } = await supabase.from('stations').select('id, status').ilike('name', fullStationName).limit(1);
    if (existingStation && existingStation.length > 0) {
      const stationId = existingStation[0].id;
      const { data: existingFuel } = await supabase.from('prices').select('id, price').eq('station_id', stationId).eq('fuel_type', resolvedFuelType).neq('status', 'Archived').limit(1);
      if (existingFuel && existingFuel.length > 0) {
        await supabase.from('prices').update({ status: 'Archived' }).eq('id', existingFuel[0].id);
        await supabase.from('prices').insert([{ station_id: stationId, fuel_type: resolvedFuelType, price: newPrice, old_price: existingFuel[0].price, status: 'Unverified', upvotes: 1 }]);
        setAlertModal({ isOpen: true, title: "Update Submitted", message: `Success! Updated ${resolvedFuelType} at ${fullStationName}. Awaiting community verification.`, isError: false });
      } else {
        await supabase.from('prices').insert([{ station_id: stationId, fuel_type: resolvedFuelType, price: newPrice, status: 'Unverified', upvotes: 1 }]);
        setAlertModal({ isOpen: true, title: "Fuel Added", message: `Success! Added ${resolvedFuelType} to ${fullStationName}. Awaiting verification.`, isError: false });
      }
    } else {
      const { data: newStation } = await supabase.from('stations').insert([{ name: fullStationName, city: cityName, status: 'Unverified' }]).select();
      if (newStation && newStation.length > 0) {
        await supabase.from('prices').insert([{ station_id: newStation[0].id, fuel_type: resolvedFuelType, price: newPrice, status: 'Unverified', upvotes: 1 }]);
        setAlertModal({ isOpen: true, title: "Station Added", message: `Thank you! ${fullStationName} is now on the map as an Unverified Location. It will be verified once 2 more drivers confirm it.`, isError: false });
      }
    }
    setBranchName(''); setPrice(''); setFuelType(''); setCustomFuelType(''); fetchPrices();
  }

  // --- DATA PREPARATION ---
  const stationsMap = {};
  rawPrices.forEach(item => {
    const st = item.stations;
    if (!stationsMap[st.id]) {
      let brand = 'Independent';
      const nameLower = st.name.toLowerCase();
      if (nameLower.includes('petron')) brand = 'Petron';
      else if (nameLower.includes('shell')) brand = 'Shell';
      else if (nameLower.includes('caltex')) brand = 'Caltex';
      else if (nameLower.includes('cleanfuel')) brand = 'Cleanfuel';
      else if (nameLower.includes('total')) brand = 'Total';
      else if (nameLower.includes('flying v')) brand = 'Flying V';
      else if (nameLower.includes('seaoil')) brand = 'SeaOil';
      else if (nameLower.includes('phoenix')) brand = 'Phoenix';
      else if (nameLower.includes('unioil')) brand = 'Unioil';
      stationsMap[st.id] = { ...st, brand: brand, prices: [] };
    }
    stationsMap[st.id].prices.push(item);
  });

  let groupedStations = Object.values(stationsMap);
  if (selectedBrand !== 'Dashboard') groupedStations = groupedStations.filter(s => s.brand === selectedBrand);
  if (searchQuery && searchQuery.trim() !== '') {
    groupedStations = groupedStations.filter(s =>
      s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      s.city.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }
  groupedStations.sort((a, b) => a.name.localeCompare(b.name));

  const brands = ['Dashboard', 'Petron', 'Shell', 'Caltex', 'Cleanfuel', 'Flying V', 'SeaOil', 'Total', 'Phoenix', 'Unioil', 'Independent'];
  const dashboardBrands = ['Petron', 'Shell', 'Caltex', 'Cleanfuel', 'Flying V', 'SeaOil', 'Total', 'Phoenix', 'Unioil'];

  const fuelDictionary = {
    Petron: ['Blaze 100', 'XCS 95', 'Xtra Advance 93', 'Super Xtra 91', 'Turbo Diesel', 'Diesel Max', 'Kerosene'],
    Shell: ['V-Power Gasoline 95', 'FuelSave 95', 'FuelSave Unleaded 91', 'V-Power Diesel', 'FuelSave Diesel', 'Kerosene'],
    Caltex: ['Platinum 95 with Techron', 'Silver 91 with Techron', 'Power Diesel with Techron D', 'Diesel with Techron D', 'Kerosene'],
    Cleanfuel: ['Premium 95', 'Clean 91', 'Diesel'],
    'Flying V': ['Gasoline 95', 'Unleaded 91', 'Biodiesel'],
    SeaOil: ['Extreme 97', 'Extreme 95', 'Extreme U 91', 'Exceed Diesel', 'Kerosene'],
    Total: ['Excellium 95', 'Premier 91', 'Excellium Diesel', 'Standard Diesel'],
    Phoenix: ['Premium 98', 'Premium 95', 'Super Regular 91', 'Biodiesel', 'Autogas (LPG)'],
    Unioil: ['Premium 97', 'Premium 95', 'Unleaded 91', 'Euro 5 Diesel'],
    Independent: ['Premium 95', 'Unleaded 91', 'Diesel', 'Kerosene'],
    Default: ['Premium 95', 'Unleaded 91', 'Diesel'],
  };
  const availableFuels = fuelDictionary[stationBrand] || fuelDictionary.Default;

  let latestUpdate = 'Loading...';
  if (rawPrices.length > 0) {
    const dates = rawPrices.map(p => new Date(p.last_updated || Date.now()).getTime());
    latestUpdate = new Date(Math.max(...dates)).toLocaleString('en-PH', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  // --- DASHBOARD AGGREGATION ---
  // Build: dashboardData[city][brand] = { stations[], summaryPrices{ fuelType: lowestPrice } }
  const dashboardData = {};
  Object.values(stationsMap).forEach(station => {
    const city = station.city;
    const brand = station.brand;
    if (!dashboardData[city]) dashboardData[city] = {};
    if (!dashboardData[city][brand]) dashboardData[city][brand] = { stations: [], summaryPrices: {} };
    dashboardData[city][brand].stations.push(station);
    station.prices.forEach(fuel => {
      if (fuel.out_of_stock_votes < 3) {
        const existing = dashboardData[city][brand].summaryPrices[fuel.fuel_type];
        if (existing === undefined || fuel.price < existing) {
          dashboardData[city][brand].summaryPrices[fuel.fuel_type] = fuel.price;
        }
      }
    });
  });

  // ─────────────────────────────────────────────────────────────
  // REUSABLE: renders a single station's expandable card with
  // all fuel rows and action buttons (used in both views)
  // ─────────────────────────────────────────────────────────────
  function StationCard({ station, showFullName = false }) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div
          onClick={() => setExpandedStationId(expandedStationId === station.id ? null : station.id)}
          className="p-4 flex justify-between items-center cursor-pointer hover:bg-gray-50"
        >
          <div>
            <h3 className="font-bold text-gray-800 text-md flex items-center flex-wrap gap-2">
              {showFullName ? station.name : station.name.replace(`${station.brand} - `, '')}
              {station.status === 'Unverified' && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-orange-100 text-orange-800 border border-orange-200 uppercase tracking-wider font-bold">⚠️ Unverified</span>
              )}
            </h3>
            <p className="text-xs text-gray-400 font-medium mt-1">{station.prices.length} Fuel Types</p>
          </div>
          <div className="text-blue-600 font-bold text-xl">{expandedStationId === station.id ? '−' : '+'}</div>
        </div>

        {expandedStationId === station.id && (
          <div className="bg-gray-50 border-t border-gray-200 p-4 flex flex-col gap-3">
            {station.prices.sort((a, b) => a.price - b.price).map(fuel => (
              <div key={fuel.id} className="flex flex-col bg-white p-3 rounded border border-gray-200 shadow-sm">
                <div className="flex justify-between items-start">
                  <div>
                    <p className="font-bold text-blue-800 text-sm flex items-center gap-2">
                      {fuel.fuel_type}
                      <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${fuel.status === 'Verified' ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-600'}`}>
                        {fuel.status}
                      </span>
                    </p>
                    <p className="text-xs text-gray-500 mt-1">{fuel.upvotes} Upvotes</p>
                  </div>
                  <div className="text-right">
                    {fuel.out_of_stock_votes >= 3 ? (
                      <p className="text-xl font-black text-red-600">OUT OF STOCK</p>
                    ) : (
                      <div className="flex flex-col items-end">
                        <p className="text-xl font-black text-gray-900">₱{fuel.price.toFixed(2)}</p>
                        {fuel.old_price && (
                          <p className="text-[10px] text-gray-400 font-medium line-through mt-0.5">Was ₱{fuel.old_price.toFixed(2)}</p>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex justify-between mt-3 pt-2 border-t border-gray-100">
                  <button
                    onClick={(e) => { e.stopPropagation(); handleUpvotePrice(fuel.id, fuel.upvotes, station.id, fuel.fuel_type); }}
                    className="text-blue-600 text-xs font-bold px-2 py-1 hover:bg-blue-50 rounded"
                  >👍 Confirm</button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setUpdateModal({ isOpen: true, priceId: fuel.id, currentPrice: fuel.price, stationId: station.id, stationName: station.name, fuelName: fuel.fuel_type }); setNewPriceInput(''); }}
                    className="text-gray-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded"
                  >✏️ Update</button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleOutOfStock(fuel.id, fuel.out_of_stock_votes); }}
                    className="text-gray-500 hover:text-red-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded transition-colors"
                  >🚩 Empty</button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleRetiredFuel(fuel.id, fuel.retired_votes); }}
                    onTouchStart={(e) => {
                      e.stopPropagation();
                      deleteHoldTimer.current = setTimeout(() => {
                        setDeletePinInput('');
                        setDeleteModal({ isOpen: true, stationId: station.id, stationName: station.name, priceId: fuel.id, fuelType: fuel.fuel_type, mode: 'fuel' });
                      }, 800);
                    }}
                    onTouchEnd={() => clearTimeout(deleteHoldTimer.current)}
                    onTouchMove={() => clearTimeout(deleteHoldTimer.current)}
                    onMouseDown={(e) => {
                      e.stopPropagation();
                      deleteHoldTimer.current = setTimeout(() => {
                        setDeletePinInput('');
                        setDeleteModal({ isOpen: true, stationId: station.id, stationName: station.name, priceId: fuel.id, fuelType: fuel.fuel_type, mode: 'fuel' });
                      }, 800);
                    }}
                    onMouseUp={() => clearTimeout(deleteHoldTimer.current)}
                    onMouseLeave={() => clearTimeout(deleteHoldTimer.current)}
                    className="text-gray-500 hover:text-orange-600 text-xs font-bold px-2 py-1 hover:bg-gray-100 rounded transition-colors select-none"
                  >🗑️ Not Sold</button>
                </div>
              </div>
            ))}
            <button
              onClick={(e) => {
                e.stopPropagation();
                setStationBrand(station.brand);
                setBranchName(station.name.replace(`${station.brand} - `, '').trim());
                window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
              }}
              className="w-full mt-1 py-2 text-xs font-bold text-blue-600 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 border-dashed transition-colors"
            >+ Add Missing Fuel Type</button>
          </div>
        )}
      </div>
    );
  }

  // --- UI RENDERING ---
  const isDashboard = selectedBrand === 'Dashboard' && !searchQuery;

  return (
    <div className="min-h-screen bg-gray-100 font-sans pb-10">

      <header className="bg-blue-800 text-white p-4 shadow-md sticky top-0 z-20">
        <h1 className="text-xl font-bold">Benguet Gas Monitor</h1>
        <p className="text-xs text-blue-200">Community-Driven Pump Prices</p>
        <p className="text-[10px] text-blue-300/60 mt-0.5 font-mono">v{__APP_VERSION__}</p>
      </header>

      {/* BRAND TABS */}
      <div className="bg-white shadow-sm border-b border-gray-200 p-3 overflow-x-auto whitespace-nowrap sticky top-[60px] z-10">
        <div className="flex gap-2">
          {brands.map(brand => (
            <button
              key={brand}
              onClick={() => { setSelectedBrand(brand); setExpandedStationId(null); setExpandedDashboardKey(null); }}
              className={`px-4 py-1.5 rounded-full text-sm font-bold border transition-colors ${selectedBrand === brand ? 'bg-blue-800 text-white border-blue-800' : 'bg-gray-50 text-gray-600 border-gray-300 hover:bg-gray-200'}`}
            >
              {brand === 'Dashboard' ? '📊 Dashboard' : brand}
            </button>
          ))}
        </div>
      </div>

      {/* SEARCH BAR */}
      <div className="bg-gray-100 px-4 py-3 sticky top-[115px] z-10 shadow-sm border-b border-gray-200 backdrop-blur-md bg-gray-100/90">
        <div className="relative max-w-md mx-auto">
          <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-gray-400">🔍</span>
          <input
            type="text"
            placeholder="Search stations, branches, or cities..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-white border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block pl-10 p-2.5 shadow-inner"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600">✖</button>
          )}
        </div>
      </div>

      <main className="max-w-md mx-auto mt-4 px-4">

        {dbError && (
          <div className="bg-red-50 border border-red-200 p-4 rounded-lg mt-4 mb-4 text-center shadow-sm">
            <h2 className="text-red-800 font-bold text-sm">Connection Error</h2>
            <p className="text-red-600 text-xs mt-1">Your network blocked the connection to our database. Try turning off your VPN or Private DNS.</p>
            <p className="text-gray-400 text-[10px] mt-2 font-mono">{dbError}</p>
          </div>
        )}

        <div className="bg-blue-50 border border-blue-200 p-4 rounded-lg mb-5 shadow-sm">
          <h2 className="text-blue-800 font-bold text-sm mb-1 flex items-center gap-1"><span>ℹ️</span> About This Data</h2>
          <p className="text-blue-900 text-xs leading-relaxed mb-3">Because actual pump prices vary by region due to logistics costs, this platform relies on <strong>local crowdsourcing</strong>. Help fellow drivers by verifying or updating prices when you fuel up!</p>
          <div className="bg-white/70 rounded px-2 py-1.5 border border-blue-100 inline-block">
            <p className="text-[10px] text-blue-800 font-bold uppercase tracking-wider">Last Database Update: <span className="text-blue-600 ml-1">{latestUpdate}</span></p>
          </div>
        </div>

        {/* ══════════════════════════════════════════════════
            DASHBOARD VIEW — City → Brand summary cards
        ══════════════════════════════════════════════════ */}
        {isDashboard ? (
          <div className="flex flex-col gap-6">
            {['Baguio City', 'La Trinidad', 'Tuba'].map(city => {
              const cityData = dashboardData[city];
              if (!cityData) return null;

              const brandsInCity = dashboardBrands.filter(b => cityData[b]);
              if (brandsInCity.length === 0) return null;

              return (
                <div key={city} className="flex flex-col gap-3">
                  {/* City header */}
                  <div className="flex items-center gap-2 pb-1 border-b-2 border-blue-800/10">
                    <span className="text-blue-800 text-lg">📍</span>
                    <h2 className="font-black text-gray-700 uppercase tracking-widest text-sm">{city}</h2>
                  </div>

                  {/* Brand summary cards */}
                  {brandsInCity.map(brand => {
                    const brandData = cityData[brand];
                    const summaryPrices = brandData.summaryPrices;
                    const isExpanded = expandedDashboardKey === `${city}|${brand}`;
                    const fuelEntries = Object.entries(summaryPrices).sort((a, b) => a[0].localeCompare(b[0]));

                    return (
                      <div key={brand} className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
                        {/* Summary card header — tap to expand */}
                        <div
                          onClick={() => {
                            setExpandedDashboardKey(isExpanded ? null : `${city}|${brand}`);
                            setExpandedStationId(null);
                          }}
                          className="p-4 cursor-pointer hover:bg-gray-50 transition-colors"
                        >
                          <div className="flex justify-between items-center mb-3">
                            <h3 className="font-black text-gray-800 text-base">{brand}</h3>
                            <div className="flex items-center gap-2">
                              <span className="text-[11px] font-bold text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                                {brandData.stations.length} {brandData.stations.length === 1 ? 'branch' : 'branches'}
                              </span>
                              <span className="text-blue-600 font-bold text-lg">{isExpanded ? '−' : '+'}</span>
                            </div>
                          </div>

                          {/* Fuel price grid */}
                          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
                            {fuelEntries.map(([fuel, p]) => (
                              <div key={fuel} className="flex justify-between items-baseline border-b border-gray-100 pb-1">
                                <span className="text-[11px] font-semibold text-gray-500 truncate mr-1 max-w-[110px]" title={fuel}>
                                  {fuel
                                    .replace('with Techron D', '')
                                    .replace('with Techron', '')
                                    .replace('Gasoline', 'Gas.')
                                    .replace('Unleaded', 'Unl.')
                                    .trim()}
                                </span>
                                <span className="text-sm font-black text-gray-900 whitespace-nowrap">₱{p.toFixed(2)}</span>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Expanded branches */}
                        {isExpanded && (
                          <div className="bg-gray-50 border-t border-gray-200 p-3 flex flex-col gap-2">
                            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest ml-1 mb-1">Branch Details</p>
                            {brandData.stations.map(station => (
                              <StationCard key={station.id} station={station} showFullName={false} />
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ) : (
          /* ══════════════════════════════════════════════════
              BRAND TAB VIEW or SEARCH RESULTS
          ══════════════════════════════════════════════════ */
          <div className="flex flex-col gap-6">
            {['Baguio City', 'La Trinidad', 'Tuba'].map(city => {
              const cityStations = groupedStations.filter(s => s.city === city);
              if (cityStations.length === 0) return null;
              return (
                <div key={city} className="flex flex-col gap-3">
                  <div className="flex items-center gap-2 pb-1 border-b-2 border-blue-800/10">
                    <span className="text-blue-800 text-lg">📍</span>
                    <h2 className="font-black text-gray-700 uppercase tracking-widest text-sm">{city}</h2>
                    <span className="ml-auto text-xs font-bold text-gray-400 bg-gray-200 px-2 py-0.5 rounded-full">{cityStations.length}</span>
                  </div>
                  {cityStations.map(station => (
                    <StationCard key={station.id} station={station} showFullName={true} />
                  ))}
                </div>
              );
            })}
          </div>
        )}

        {/* ADD STATION / FUEL FORM */}
        <div className="mt-8 bg-white p-4 rounded-lg shadow-sm border border-gray-300">
          <h2 className="text-md font-bold text-gray-800 mb-1">Missing a Station or Fuel?</h2>
          <p className="text-xs text-gray-500 mb-3 leading-relaxed">Help keep your community moving. Submit missing data below—it will be published once 3 local drivers verify it.</p>
          <form onSubmit={handleAddStation} className="flex flex-col gap-2">
            <div className="flex gap-2">
              <select className="border p-2 rounded text-sm w-1/3 bg-gray-50 font-bold text-blue-800" value={stationBrand} onChange={(e) => { setStationBrand(e.target.value); setFuelType(''); }}>
                {brands.filter(b => b !== 'Dashboard').map(b => <option key={b} value={b}>{b}</option>)}
              </select>
              <input type="text" placeholder="Branch (e.g. Loakan Road)" required className="border p-2 rounded text-sm w-2/3 bg-gray-50" value={branchName} onChange={(e) => setBranchName(e.target.value)} />
            </div>
            <select className="border p-2 rounded text-sm bg-gray-50" value={cityName} onChange={(e) => setCityName(e.target.value)}>
              <option>Baguio City</option><option>La Trinidad</option><option>Tuba</option>
            </select>
            <div className="flex flex-col gap-2">
              <select
                required={fuelType !== 'Other'}
                className="border p-2 rounded text-sm bg-gray-50 text-gray-700"
                value={fuelType}
                onChange={(e) => { setFuelType(e.target.value); setCustomFuelType(''); }}
              >
                <option value="" disabled hidden>Select Fuel Type</option>
                {availableFuels.map(f => <option key={f} value={f}>{f}</option>)}
                <option value="Other">✏️ Other (specify below)</option>
              </select>
              {fuelType === 'Other' && (
                <input
                  type="text"
                  required
                  placeholder="e.g. Autogas, E10, Bio-Ethanol..."
                  className="border p-2 rounded text-sm bg-gray-50 border-blue-400 ring-1 ring-blue-300"
                  value={customFuelType}
                  onChange={(e) => setCustomFuelType(e.target.value)}
                />
              )}
            </div>
            <input
              type="text"
              inputMode="decimal"
              placeholder="Price (e.g. 68.50)"
              required
              className="border p-2 rounded text-sm w-1/2 bg-gray-50"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
            <button type="submit" className="bg-blue-800 text-white font-bold py-2 rounded mt-2 hover:bg-blue-900">Submit Addition</button>
          </form>
        </div>

        {/* ── UPDATE PRICE MODAL ── */}
        {updateModal.isOpen && (
          <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="bg-white rounded-xl shadow-lg w-full max-w-sm p-5 border border-gray-200">
              <h3 className="text-lg font-bold text-gray-800 mb-1">Update Price</h3>
              <p className="text-sm text-gray-600 mb-4">{updateModal.stationName} — <span className="font-bold text-blue-700">{updateModal.fuelName}</span></p>
              <form onSubmit={submitPriceUpdate} className="flex flex-col gap-3">
                <div>
                  <label
                    className="text-xs font-bold text-gray-500 uppercase tracking-wider select-none cursor-default"
                    onTouchStart={() => { longPressTimer.current = setTimeout(() => { if (newPriceInput && !newPriceInput.endsWith('*')) setNewPriceInput(newPriceInput + '*'); }, 800); }}
                    onTouchEnd={() => clearTimeout(longPressTimer.current)}
                    onTouchMove={() => clearTimeout(longPressTimer.current)}
                    onMouseDown={() => { longPressTimer.current = setTimeout(() => { if (newPriceInput && !newPriceInput.endsWith('*')) setNewPriceInput(newPriceInput + '*'); }, 800); }}
                    onMouseUp={() => clearTimeout(longPressTimer.current)}
                    onMouseLeave={() => clearTimeout(longPressTimer.current)}
                  >New Price (₱)</label>
                  <input
                    type="text"
                    inputMode="decimal"
                    autoFocus
                    required
                    placeholder="e.g. 68.50"
                    className={`w-full mt-1 border p-3 rounded-lg text-lg font-black bg-gray-50 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-colors ${newPriceInput.endsWith('*') ? 'border-yellow-400 text-yellow-900 bg-yellow-50' : 'border-gray-300 text-gray-900'}`}
                    value={newPriceInput}
                    onChange={(e) => setNewPriceInput(e.target.value)}
                  />
                  <p className="text-[10px] text-gray-500 mt-2 italic text-center leading-tight">
                    <span className="not-italic mr-1">📍</span>Help keep your community moving. Your anonymous update goes live once 3 local drivers verify it.
                  </p>
                </div>
                <div className="flex gap-2 mt-2">
                  <button type="button" onClick={() => setUpdateModal({ isOpen: false })} className="flex-1 py-2.5 rounded-lg font-bold text-gray-600 bg-gray-100 hover:bg-gray-200 transition-colors">Cancel</button>
                  <button type="submit" className="flex-1 py-2.5 rounded-lg font-bold text-white bg-blue-600 hover:bg-blue-700 transition-colors">Submit Update</button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* ── ALERT MODAL ── */}
        {alertModal.isOpen && (
          <div className="fixed inset-0 bg-black/60 z-[60] flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="bg-white rounded-xl shadow-lg w-full max-w-sm p-6 border border-gray-200 text-center">
              <div className={`mx-auto flex items-center justify-center h-12 w-12 rounded-full mb-4 ${alertModal.isError ? 'bg-red-100' : 'bg-green-100'}`}>
                <span className="text-2xl">{alertModal.isError ? '❌' : '✅'}</span>
              </div>
              <h3 className="text-lg font-bold text-gray-900 mb-2">{alertModal.title}</h3>
              <p className="text-sm text-gray-600 mb-6 leading-relaxed">{alertModal.message}</p>
              <button onClick={() => setAlertModal({ ...alertModal, isOpen: false })} className={`w-full py-3 rounded-lg font-bold text-white transition-colors ${alertModal.isError ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'}`}>
                Got it
              </button>
            </div>
          </div>
        )}

        {/* ── ADMIN DELETE MODAL ── */}
        {deleteModal.isOpen && (
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6">
              <div className="flex items-center gap-3 mb-1">
                <span className="text-2xl">🗑️</span>
                <h2 className="text-lg font-black text-gray-900">Admin Delete</h2>
              </div>
              <p className="text-sm text-gray-500 mb-1">
                <span className="font-semibold text-gray-700">{deleteModal.stationName}</span>
              </p>
              <p className="text-sm text-red-600 font-bold mb-4">
                {deleteModal.mode === 'fuel'
                  ? `Delete "${deleteModal.fuelType}" from this station?`
                  : `Delete this entire station and all its fuel prices?`}
              </p>
              <div className="flex gap-2 mb-4">
                <button
                  type="button"
                  onClick={() => setDeleteModal(m => ({ ...m, mode: 'fuel' }))}
                  className={`flex-1 py-2 rounded-lg text-xs font-bold border transition-colors ${deleteModal.mode === 'fuel' ? 'bg-orange-100 text-orange-800 border-orange-300' : 'bg-gray-50 text-gray-400 border-gray-200'}`}
                >⛽ This Fuel Only</button>
                <button
                  type="button"
                  onClick={() => setDeleteModal(m => ({ ...m, mode: 'station' }))}
                  className={`flex-1 py-2 rounded-lg text-xs font-bold border transition-colors ${deleteModal.mode === 'station' ? 'bg-red-100 text-red-800 border-red-300' : 'bg-gray-50 text-gray-400 border-gray-200'}`}
                >🏪 Entire Station</button>
              </div>
              <label className="text-xs font-bold text-gray-500 uppercase tracking-wider">Enter Admin PIN</label>
              <input
                type="password"
                inputMode="numeric"
                autoFocus
                maxLength={6}
                placeholder="••••"
                className="w-full mt-1 mb-4 border border-gray-300 p-3 rounded-lg text-lg font-black text-center tracking-widest text-gray-900 bg-gray-50 focus:ring-2 focus:ring-red-400 focus:border-red-400 outline-none"
                value={deletePinInput}
                onChange={(e) => setDeletePinInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleAdminDelete(); }}
              />
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => { setDeleteModal({ isOpen: false, stationId: null, stationName: '', priceId: null, fuelType: '', mode: 'fuel' }); setDeletePinInput(''); }}
                  className="flex-1 py-3 rounded-xl border border-gray-200 text-gray-600 font-bold text-sm hover:bg-gray-50 transition-colors"
                >Cancel</button>
                <button
                  type="button"
                  onClick={handleAdminDelete}
                  className="flex-1 py-3 rounded-xl bg-red-600 text-white font-bold text-sm hover:bg-red-700 transition-colors"
                >Delete</button>
              </div>
            </div>
          </div>
        )}

      </main>
    </div>
  );
}