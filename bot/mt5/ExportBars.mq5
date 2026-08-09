//+------------------------------------------------------------------+
//|  ExportBars.mq5                                                  |
//|  Exporte les bougies et la spécification du symbole pour smcbot. |
//|                                                                  |
//|  Aucun Python requis : ce script tourne dans MetaTrader 5 et     |
//|  écrit deux fichiers dans MQL5/Files du dossier de données du    |
//|  terminal (Fichier -> Ouvrir le dossier de données) :            |
//|                                                                  |
//|    <SYMBOLE>_<UT>.csv   les bougies, prêtes pour smcbot          |
//|    <SYMBOLE>_spec.json  les caractéristiques du contrat          |
//|                                                                  |
//|  Utilisation : copier ce fichier dans MQL5/Scripts, compiler     |
//|  (F7) dans MetaEditor, puis glisser le script sur un graphique.  |
//+------------------------------------------------------------------+
#property copyright "smcbot"
#property version   "1.00"
#property script_show_inputs
#property description "Exporte les bougies d'un symbole en CSV pour smcbot."

input string          InpSymbol    = "";           // Symbole (vide = celui du graphique)
input ENUM_TIMEFRAMES InpTimeframe = PERIOD_M1;    // Unite de temps
input int             InpBars      = 50000;        // Nombre de bougies

//+------------------------------------------------------------------+
int WaitForHistory(const string symbol, const ENUM_TIMEFRAMES tf,
                   MqlRates &rates[], const int wanted)
  {
   // La premiere demande declenche souvent le telechargement de l'historique :
   // on retente quelques fois avant d'abandonner.
   for(int essai = 0; essai < 10; essai++)
     {
      int copied = CopyRates(symbol, tf, 0, wanted, rates);
      if(copied > 0)
         return(copied);
      Print("Historique en cours de chargement... (essai ", essai + 1, "/10)");
      Sleep(1000);
     }
   return(-1);
  }

//+------------------------------------------------------------------+
string TimeframeName(const ENUM_TIMEFRAMES tf)
  {
   string nom = EnumToString(tf);          // ex. "PERIOD_M1"
   return(StringSubstr(nom, 7));           // -> "M1"
  }

//+------------------------------------------------------------------+
bool WriteSpec(const string symbol)
  {
   int digits       = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double point     = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double contract  = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double tick_val  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double min_lot   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max_lot   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lot_step  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   long   spread    = SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   double swap_long = SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG);
   double swap_shrt = SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT);
   long   swap_mode = SymbolInfoInteger(symbol, SYMBOL_SWAP_MODE);

   // Valeur monetaire d'un point pour 1 lot, dans la devise du compte.
   // C'est LA valeur qui doit etre juste : tout le dimensionnement en depend.
   double value_per_point = tick_val;
   if(tick_size > 0.0)
      value_per_point = tick_val * (point / tick_size);

   string path = symbol + "_spec.json";
   int handle = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      Print("Impossible d'ecrire ", path, " (erreur ", GetLastError(), ")");
      return(false);
     }

   FileWrite(handle, "{");
   FileWrite(handle, "  \"name\": \"" + symbol + "\",");
   FileWrite(handle, "  \"digits\": " + IntegerToString(digits) + ",");
   FileWrite(handle, "  \"point\": " + DoubleToString(point, 10) + ",");
   FileWrite(handle, "  \"contract_size\": " + DoubleToString(contract, 2) + ",");
   FileWrite(handle, "  \"value_per_point_per_lot\": " + DoubleToString(value_per_point, 6) + ",");
   FileWrite(handle, "  \"min_lot\": " + DoubleToString(min_lot, 2) + ",");
   FileWrite(handle, "  \"max_lot\": " + DoubleToString(max_lot, 2) + ",");
   FileWrite(handle, "  \"lot_step\": " + DoubleToString(lot_step, 2) + ",");
   FileWrite(handle, "  \"spread_points\": " + IntegerToString((int)spread) + ",");
   // Le mode de calcul du swap varie selon le courtier : seul le mode 1
   // (SYMBOL_SWAP_MODE_POINTS) se reporte tel quel dans la configuration.
   FileWrite(handle, "  \"swap_long_points\": " +
             DoubleToString(swap_mode == 1 ? swap_long : 0.0, 4) + ",");
   FileWrite(handle, "  \"swap_short_points\": " +
             DoubleToString(swap_mode == 1 ? swap_shrt : 0.0, 4) + ",");
   FileWrite(handle, "  \"commission_per_lot\": 0.0");
   FileWrite(handle, "}");
   FileClose(handle);

   Print("--- Specification de ", symbol, " ---");
   Print("digits = ", digits, "   point = ", DoubleToString(point, 10));
   Print("contrat = ", DoubleToString(contract, 2),
         "   valeur du point pour 1 lot = ", DoubleToString(value_per_point, 4));
   Print("lot min/max/pas = ", DoubleToString(min_lot, 2), " / ",
         DoubleToString(max_lot, 2), " / ", DoubleToString(lot_step, 2));
   Print("spread actuel = ", spread, " points");
   Print("swap long/court = ", DoubleToString(swap_long, 4), " / ",
         DoubleToString(swap_shrt, 4), "   (mode ", swap_mode,
         ", 1 = points ; tout autre mode n'est PAS reporte automatiquement)");
   if(swap_mode != 1)
      Print("ATTENTION : swap exprime autrement qu'en points. Convertis-le a la ",
            "main dans swap_long_points / swap_short_points.");
   Print("Commission : non exposee par MT5, a demander a ton courtier.");
   Print("Fichier ecrit : ", path);
   return(true);
  }

//+------------------------------------------------------------------+
void OnStart()
  {
   string symbol = (InpSymbol == "") ? _Symbol : InpSymbol;

   if(!SymbolSelect(symbol, true))
     {
      Print("Symbole introuvable : ", symbol);
      return;
     }
   if(InpBars <= 0)
     {
      Print("Le nombre de bougies doit etre positif.");
      return;
     }

   MqlRates rates[];
   ArraySetAsSeries(rates, false);          // ordre chronologique croissant

   int copied = WaitForHistory(symbol, InpTimeframe, rates, InpBars);
   if(copied <= 0)
     {
      Print("Aucune bougie recuperee (erreur ", GetLastError(), "). ",
            "Ouvre un graphique ", symbol, " sur cette unite de temps et ",
            "fais defiler vers la gauche pour charger l'historique.");
      return;
     }

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   string path = symbol + "_" + TimeframeName(InpTimeframe) + ".csv";

   int handle = FileOpen(path, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
     {
      Print("Impossible d'ecrire ", path, " (erreur ", GetLastError(), ")");
      return;
     }

   FileWrite(handle, "time", "open", "high", "low", "close", "volume");
   for(int i = 0; i < copied; i++)
     {
      FileWrite(handle,
                TimeToString(rates[i].time, TIME_DATE | TIME_SECONDS),
                DoubleToString(rates[i].open,  digits),
                DoubleToString(rates[i].high,  digits),
                DoubleToString(rates[i].low,   digits),
                DoubleToString(rates[i].close, digits),
                IntegerToString((int)rates[i].tick_volume));
     }
   FileClose(handle);

   Print(copied, " bougies ", symbol, " ", TimeframeName(InpTimeframe),
         " ecrites dans ", path);
   Print("Du ", TimeToString(rates[0].time, TIME_DATE | TIME_MINUTES),
         " au ", TimeToString(rates[copied - 1].time, TIME_DATE | TIME_MINUTES));
   Print("ATTENTION : ces horodatages sont a l'heure du SERVEUR, pas en UTC. ",
         "Lance `smcbot check` pour trouver le decalage a appliquer.");

   WriteSpec(symbol);

   Print("Fichiers disponibles dans : Fichier -> Ouvrir le dossier de donnees -> MQL5\\Files");
  }
//+------------------------------------------------------------------+
