//+------------------------------------------------------------------+
//|  ScalpXAU.mq5                                                    |
//|  Scalping tick par tick sur l'or, avec garde-fous de cout.       |
//|                                                                  |
//|  Toute la decision se prend dans OnTick(), sur le flux de ticks  |
//|  et non sur des bougies fermees. Le signal est un desequilibre   |
//|  directionnel de courte duree, filtre par son efficacite et par  |
//|  le cout de transaction du moment.                               |
//|                                                                  |
//|  Principe directeur : le spread est fixe, le mouvement capturable|
//|  croit comme la racine du temps de detention. Toute condition    |
//|  d'entree est donc exprimee en MULTIPLES DU SPREAD COURANT, pas  |
//|  en points absolus. Un seuil en points absolus serait juste a    |
//|  un moment de la journee et faux a tous les autres.              |
//+------------------------------------------------------------------+
#property copyright "smcbot"
#property version   "1.00"
#property description "Scalping tick par tick XAUUSD - entrees conditionnees au cout reel"

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>

//+------------------------------------------------------------------+
//| Parametres                                                       |
//+------------------------------------------------------------------+
input group "--- Instrument ---"
input string InpSymbol            = "";      // Symbole (vide = celui du graphique)
input ulong  InpMagic             = 20260809; // Magic number (isole cet EA)

input group "--- Signal ---"
input int    InpWindowMs          = 1500;    // Fenetre d'observation du flux (ms)
input int    InpMinTicks          = 8;       // Ticks minimum dans la fenetre
input double InpMinImpulseSpreads = 2.0;     // Impulsion mini, en multiples du spread
input double InpMinEfficiency     = 0.60;    // Directionnalite mini (0-1)
input int    InpCooldownAfterMs   = 2000;    // Delai mini entre deux entrees (ms)

input group "--- Sortie ---"
input int    InpStopLossPoints    = 120;     // Stop loss (points)
input double InpTakeProfitRatio   = 1.5;     // Take profit, en multiples du stop
input int    InpMaxHoldSeconds    = 30;      // Duree maximale de detention (s)
input bool   InpExitOnReversal    = true;    // Sortir si l'impulsion s'inverse

input group "--- Cout de transaction ---"
input int    InpMaxSpreadPoints   = 35;      // Spread maximal tolere (points)
input double InpMaxCostRatio      = 0.30;    // Part maxi du risque absorbee par le spread
input int    InpDeviationPoints   = 10;      // Slippage tolere (points)

input group "--- Risque ---"
input double InpRiskPercent       = 0.5;     // Risque par trade (% du capital)
input double InpMaxDailyLossPct   = 2.0;     // Perte journaliere maximale (%)
input double InpMaxDrawdownPct    = 10.0;    // Drawdown maximal depuis le lancement (%)
input int    InpMaxConsecLosses   = 5;       // Pertes consecutives avant pause
input int    InpCooldownMinutes   = 15;      // Duree de la pause (minutes)
input int    InpMaxOpenPositions  = 1;       // Positions simultanees

input group "--- Horaires (heure serveur) ---"
input string InpSessionStart      = "00:00"; // Debut de session
input string InpSessionEnd        = "23:59"; // Fin de session
input bool   InpAvoidNews         = true;    // Couper autour des annonces
input string InpNewsTimes         = "12:30,14:00,14:30,18:00"; // Heures a eviter
input int    InpNewsBlackoutMin   = 2;       // Minutes avant/apres l'annonce

input group "--- Divers ---"
input int    InpMaxRetries        = 3;       // Tentatives en cas d'echec d'ordre
input bool   InpVerboseLog        = true;    // Journaliser chaque trade

//+------------------------------------------------------------------+
//| Etat global                                                      |
//+------------------------------------------------------------------+
CTrade        trade;
CPositionInfo pos;

string   g_symbol       = "";
int      g_digits       = 2;
double   g_point        = 0.01;
double   g_tick_size    = 0.01;
double   g_tick_value   = 1.0;
double   g_value_point  = 1.0;   // valeur d'un point pour 1 lot, devise du compte
double   g_vol_min      = 0.01;
double   g_vol_max      = 100.0;
double   g_vol_step     = 0.01;
long     g_stops_level  = 0;     // distance minimale imposee par le courtier
long     g_freeze_level = 0;

// Tampon circulaire de ticks. Taille fixe : OnTick() ne doit jamais allouer.
#define TICK_BUFFER 1024
struct TickSample
  {
   double            mid;
   double            spread_pts;
   ulong             time_msc;
  };
TickSample g_ticks[TICK_BUFFER];
int      g_tick_head    = 0;     // prochain emplacement a ecrire
int      g_tick_count   = 0;

// Position suivie
ulong    g_ticket       = 0;
datetime g_open_time    = 0;
double   g_open_spread  = 0.0;
int      g_open_dir     = 0;     // +1 achat, -1 vente
string   g_open_reason  = "";

// Compteurs de risque
double   g_start_equity = 0.0;
double   g_day_start_balance = 0.0;
int      g_day          = -1;
int      g_consec_losses = 0;
datetime g_cooldown_until = 0;
ulong    g_last_entry_msc = 0;
bool     g_halted       = false;
string   g_halt_reason  = "";

// Statistiques de session
int      g_trades = 0, g_wins = 0;
double   g_gross_profit = 0.0, g_gross_loss = 0.0;
double   g_sum_hold = 0.0;

// Heures d'annonces, pre-decoupees a l'init (jamais reparsees dans OnTick)
int      g_news_minutes[];

//+------------------------------------------------------------------+
//| Utilitaires                                                      |
//+------------------------------------------------------------------+

//--- "HH:MM" -> minutes depuis minuit, -1 si invalide
int ParseHhMm(const string texte)
  {
   string parts[];
   if(StringSplit(texte, ':', parts) != 2)
      return(-1);
   int h = (int)StringToInteger(parts[0]);
   int m = (int)StringToInteger(parts[1]);
   if(h < 0 || h > 23 || m < 0 || m > 59)
      return(-1);
   return(h * 60 + m);
  }

//--- Minutes ecoulees depuis minuit, heure serveur
int MinutesOfDay(const datetime quand)
  {
   MqlDateTime dt;
   TimeToStruct(quand, dt);
   return(dt.hour * 60 + dt.min);
  }

//--- Resolution du symbole : parametre, sinon graphique, sinon recherche
string ResolveSymbol()
  {
   if(StringLen(InpSymbol) > 0)
      return(InpSymbol);

   // Le graphique porte deja le bon symbole dans le cas normal.
   if(StringFind(_Symbol, "XAU") >= 0 || StringFind(_Symbol, "GOLD") >= 0)
      return(_Symbol);

   // Sinon on cherche dans la liste du courtier : les noms varient
   // (XAUUSD, GOLD, XAUUSD.m, XAUUSD.a...).
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {
      string nom = SymbolName(i, false);
      if(StringFind(nom, "XAU") >= 0)
         return(nom);
     }
   return(_Symbol);
  }

//+------------------------------------------------------------------+
//| Initialisation                                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_symbol = ResolveSymbol();

   if(!SymbolSelect(g_symbol, true))
     {
      Print("ERREUR : symbole ", g_symbol, " indisponible chez ce courtier.");
      return(INIT_FAILED);
     }

   //--- Cache des proprietes du contrat. Rien de tout ceci n'est relu
   //--- dans OnTick() : ce sont des appels systeme, et a plusieurs ticks
   //--- par seconde leur cout n'est pas negligeable.
   g_digits      = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   g_point       = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   g_tick_size   = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_SIZE);
   g_tick_value  = SymbolInfoDouble(g_symbol, SYMBOL_TRADE_TICK_VALUE);
   g_vol_min     = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   g_vol_max     = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   g_vol_step    = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   g_stops_level = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL);
   g_freeze_level= SymbolInfoInteger(g_symbol, SYMBOL_TRADE_FREEZE_LEVEL);

   if(g_point <= 0.0)
     {
      Print("ERREUR : point nul pour ", g_symbol, ".");
      return(INIT_FAILED);
     }

   // Valeur d'un point pour 1 lot. MT5 expose la valeur d'un TICK ; sur la
   // plupart des symboles tick et point coincident, mais pas tous. Cette
   // conversion decide de tout le dimensionnement.
   g_value_point = (g_tick_size > 0.0)
                   ? g_tick_value * (g_point / g_tick_size)
                   : g_tick_value;

   if(g_value_point <= 0.0)
     {
      Print("ERREUR : valeur du point nulle. Dimensionnement impossible.");
      return(INIT_FAILED);
     }

   //--- Validation des parametres, avant toute prise de risque.
   if(InpStopLossPoints <= 0)
     {
      Print("ERREUR : le stop loss doit etre strictement positif. ",
            "Cet EA n'ouvre jamais de position sans stop.");
      return(INIT_FAILED);
     }
   if(InpRiskPercent <= 0.0 || InpRiskPercent > 5.0)
     {
      Print("ERREUR : RiskPercent doit etre dans ]0 ; 5]. Valeur recue : ",
            DoubleToString(InpRiskPercent, 2));
      return(INIT_FAILED);
     }
   if(InpTakeProfitRatio <= 0.0)
     {
      Print("ERREUR : TakeProfitRatio doit etre strictement positif.");
      return(INIT_FAILED);
     }
   if(InpMaxHoldSeconds <= 0)
     {
      Print("ERREUR : MaxHoldSeconds doit etre strictement positif.");
      return(INIT_FAILED);
     }
   if(ParseHhMm(InpSessionStart) < 0 || ParseHhMm(InpSessionEnd) < 0)
     {
      Print("ERREUR : SessionStart / SessionEnd doivent etre au format HH:MM.");
      return(INIT_FAILED);
     }

   //--- Le stop demande respecte-t-il le minimum du courtier ?
   if(g_stops_level > 0 && InpStopLossPoints < (int)g_stops_level)
     {
      Print("ERREUR : stop de ", InpStopLossPoints, " points, alors que ",
            g_symbol, " impose au minimum ", g_stops_level, " points.");
      Print("  Le courtier rejetterait chaque ordre. Augmente InpStopLossPoints.");
      return(INIT_FAILED);
     }

   //--- Garde-fou de coherence economique.
   //--- Le spread est preleve sur le risque du trade. S'il en represente une
   //--- part demesuree, aucune qualite de signal ne peut compenser : le taux
   //--- de reussite exige devient inatteignable. Mieux vaut refuser de
   //--- demarrer que de laisser tourner une configuration perdante d'avance.
   double spread_now = (double)SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
   if(spread_now <= 0.0)
     {
      MqlTick t;
      if(SymbolInfoTick(g_symbol, t) && t.ask > 0 && t.bid > 0)
         spread_now = (t.ask - t.bid) / g_point;
     }
   if(spread_now > 0.0)
     {
      double ratio = spread_now / (double)InpStopLossPoints;
      double requis = (1.0 + ratio) / (1.0 + InpTakeProfitRatio);
      PrintFormat("Cout de transaction : %.0f points de spread sur %d points de "
                  "stop = %.1f%% du risque.", spread_now, InpStopLossPoints,
                  ratio * 100.0);
      PrintFormat("  Taux de reussite necessaire a l'equilibre : %.1f%% "
                  "(%.1f%% sans frais).", requis * 100.0,
                  100.0 / (1.0 + InpTakeProfitRatio));
      if(ratio > InpMaxCostRatio)
        {
         PrintFormat("REFUS DE DEMARRER : les frais absorbent %.1f%% du risque, "
                     "au-dela du plafond de %.1f%%.", ratio * 100.0,
                     InpMaxCostRatio * 100.0);
         PrintFormat("  Deux issues : elargir le stop a au moins %.0f points, "
                     "ou trader quand le spread est plus serre.",
                     spread_now / InpMaxCostRatio);
         Print("  Ce n'est pas un reglage a forcer : c'est de l'arithmetique.");
         return(INIT_FAILED);
        }
     }
   else
      Print("AVERTISSEMENT : spread non mesurable (marche ferme ?). ",
            "Le controle de coherence economique sera fait au premier tick.");

   //--- Heures d'annonces, decoupees une fois pour toutes.
   ArrayResize(g_news_minutes, 0);
   if(InpAvoidNews && StringLen(InpNewsTimes) > 0)
     {
      string morceaux[];
      int n = StringSplit(InpNewsTimes, ',', morceaux);
      for(int i = 0; i < n; i++)
        {
         string t = morceaux[i];
         StringTrimLeft(t);
         StringTrimRight(t);
         int minute = ParseHhMm(t);
         if(minute >= 0)
           {
            int taille = ArraySize(g_news_minutes);
            ArrayResize(g_news_minutes, taille + 1);
            g_news_minutes[taille] = minute;
           }
         else
            Print("AVERTISSEMENT : heure d'annonce ignoree (format HH:MM attendu) : ", t);
        }
     }

   //--- Execution
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviationPoints);
   trade.SetTypeFillingBySymbol(g_symbol);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   //--- Etat du compte
   g_start_equity       = AccountInfoDouble(ACCOUNT_EQUITY);
   g_day_start_balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   g_day                = -1;
   g_tick_head          = 0;
   g_tick_count         = 0;

   //--- Reprise apres redemarrage : recuperer une position deja ouverte.
   AdoptExistingPosition();

   PrintFormat("ScalpXAU demarre sur %s (digits=%d, point=%.5f, valeur du point "
               "pour 1 lot=%.4f).", g_symbol, g_digits, g_point, g_value_point);
   PrintFormat("Lot min/max/pas : %.2f / %.2f / %.2f. Stops level : %d points.",
               g_vol_min, g_vol_max, g_vol_step, (int)g_stops_level);

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Reprise d'une position laissee par une session precedente        |
//+------------------------------------------------------------------+
void AdoptExistingPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!pos.SelectByIndex(i))
         continue;
      if(pos.Symbol() != g_symbol || pos.Magic() != InpMagic)
         continue;

      g_ticket      = pos.Ticket();
      g_open_time   = (datetime)pos.Time();
      g_open_dir    = (pos.PositionType() == POSITION_TYPE_BUY) ? 1 : -1;
      g_open_spread = 0.0;
      g_open_reason = "reprise apres redemarrage";

      // Le chrono est reconstruit depuis l'heure d'ouverture reelle, pas
      // depuis maintenant : sinon une position vieille de dix minutes
      // repartirait pour MaxHoldSeconds.
      PrintFormat("Position #%I64u reprise (ouverte le %s, deja %d s).",
                  g_ticket, TimeToString(g_open_time, TIME_DATE | TIME_SECONDS),
                  (int)(TimeCurrent() - g_open_time));
      return;
     }
  }

//+------------------------------------------------------------------+
//| Arret                                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   PrintSessionStats();
  }

void PrintSessionStats()
  {
   if(g_trades == 0)
     {
      Print("Aucun trade sur cette session.");
      return;
     }
   double pf = (g_gross_loss > 0.0) ? g_gross_profit / g_gross_loss : 0.0;
   double net = g_gross_profit - g_gross_loss;
   PrintFormat("--- Statistiques de session ---");
   PrintFormat("Trades : %d | Gagnants : %d (%.1f%%)", g_trades, g_wins,
               100.0 * g_wins / g_trades);
   PrintFormat("Profit factor : %.2f | Resultat net : %.2f %s",
               pf, net, AccountInfoString(ACCOUNT_CURRENCY));
   PrintFormat("Trade moyen : %.2f %s | Duree moyenne : %.1f s",
               net / g_trades, AccountInfoString(ACCOUNT_CURRENCY),
               g_sum_hold / g_trades);
   if(g_halted)
      PrintFormat("EA a l'arret : %s", g_halt_reason);
  }

//+------------------------------------------------------------------+
//| Boucle principale                                                |
//+------------------------------------------------------------------+
void OnTick()
  {
   //--- Sans connexion, on ne prend aucune decision : les ordres
   //--- s'empileraient a l'aveugle et le prix lu serait perime.
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      return;

   MqlTick tick;
   if(!SymbolInfoTick(g_symbol, tick))
      return;
   if(tick.bid <= 0.0 || tick.ask <= 0.0)
      return;

   double spread_pts = (tick.ask - tick.bid) / g_point;
   PushTick(tick, spread_pts);

   RollDay();
   DetectClosedPosition();

   //--- Gestion de la position en cours avant tout nouveau signal.
   if(g_ticket != 0)
     {
      ManageOpenPosition(tick, spread_pts);
      return;
     }

   if(g_halted)
      return;
   if(!TradingAllowedNow(spread_pts))
      return;

   //--- Signal
   int    direction = 0;
   double impulsion = 0.0, efficacite = 0.0;
   if(!ComputeSignal(spread_pts, direction, impulsion, efficacite))
      return;

   string raison = StringFormat("impulsion %.0f pts (%.1fx spread), efficacite %.2f",
                                impulsion, impulsion / MathMax(spread_pts, 1.0),
                                efficacite);
   OpenPosition(direction, tick, spread_pts, raison);
  }

//+------------------------------------------------------------------+
//| Tampon de ticks                                                  |
//+------------------------------------------------------------------+
void PushTick(const MqlTick &tick, const double spread_pts)
  {
   g_ticks[g_tick_head].mid        = (tick.bid + tick.ask) / 2.0;
   g_ticks[g_tick_head].spread_pts = spread_pts;
   g_ticks[g_tick_head].time_msc   = (ulong)tick.time_msc;

   g_tick_head = (g_tick_head + 1) % TICK_BUFFER;
   if(g_tick_count < TICK_BUFFER)
      g_tick_count++;
  }

//--- Index reel du i-eme tick le plus recent (0 = dernier)
int TickIndex(const int recul)
  {
   return((g_tick_head - 1 - recul + 2 * TICK_BUFFER) % TICK_BUFFER);
  }

//+------------------------------------------------------------------+
//| Signal : impulsion directionnelle rapportee au spread            |
//|                                                                  |
//| Deux mesures sur la fenetre glissante :                          |
//|   - le deplacement NET  : ou le prix est alle ;                  |
//|   - le chemin PARCOURU  : combien il a bouge pour y aller.       |
//| Leur rapport est l'efficacite. Proche de 1, le prix avance en    |
//| ligne droite (un intervenant travaille un ordre) ; proche de 0,  |
//| il oscille sans aller nulle part — le cas majoritaire, et celui  |
//| ou le spread se paie pour rien.                                  |
//|                                                                  |
//| Le seuil d'impulsion est exprime en MULTIPLES DU SPREAD COURANT. |
//| C'est le point central : exiger un deplacement de N fois le cout |
//| avant d'entrer est la seule facon de ne pas dependre d'un seuil  |
//| en points qui serait faux des que le spread bouge.               |
//+------------------------------------------------------------------+
bool ComputeSignal(const double spread_pts, int &direction,
                   double &impulsion, double &efficacite)
  {
   direction  = 0;
   impulsion  = 0.0;
   efficacite = 0.0;

   if(g_tick_count < InpMinTicks)
      return(false);

   int dernier = TickIndex(0);
   ulong maintenant = g_ticks[dernier].time_msc;
   double prix_fin  = g_ticks[dernier].mid;

   //--- Remonter la fenetre. On s'arrete des que le tick est trop vieux :
   //--- aucune boucle sur tout l'historique, seulement sur la fenetre.
   double chemin  = 0.0;
   double prix_debut = prix_fin;
   int    utilises = 0;

   for(int recul = 1; recul < g_tick_count; recul++)
     {
      int idx      = TickIndex(recul);
      int idx_plus = TickIndex(recul - 1);

      if(maintenant - g_ticks[idx].time_msc > (ulong)InpWindowMs)
         break;

      chemin    += MathAbs(g_ticks[idx_plus].mid - g_ticks[idx].mid);
      prix_debut = g_ticks[idx].mid;
      utilises++;
     }

   if(utilises < InpMinTicks - 1 || chemin <= 0.0)
      return(false);

   double net_prix = prix_fin - prix_debut;
   impulsion  = MathAbs(net_prix) / g_point;
   efficacite = MathAbs(net_prix) / chemin;

   if(impulsion < InpMinImpulseSpreads * spread_pts)
      return(false);
   if(efficacite < InpMinEfficiency)
      return(false);

   direction = (net_prix > 0.0) ? 1 : -1;
   return(true);
  }

//+------------------------------------------------------------------+
//| Autorisations                                                    |
//+------------------------------------------------------------------+
bool TradingAllowedNow(const double spread_pts)
  {
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      return(false);
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
      return(false);

   //--- Le filtre le plus important de tout l'EA.
   if(spread_pts > (double)InpMaxSpreadPoints)
      return(false);

   //--- Coherence economique, reevaluee a chaque tick : le spread varie,
   //--- donc un stop acceptable a 9h ne l'est plus a 23h.
   if(spread_pts / (double)InpStopLossPoints > InpMaxCostRatio)
      return(false);

   if(PositionsCountMine() >= InpMaxOpenPositions)
      return(false);
   if(TimeCurrent() < g_cooldown_until)
      return(false);

   //--- Delai minimal entre deux entrees : sans lui, une meme impulsion
   //--- declencherait plusieurs entrees successives sur les memes ticks.
   ulong maintenant = (g_tick_count > 0) ? g_ticks[TickIndex(0)].time_msc : 0;
   if(g_last_entry_msc > 0 && maintenant - g_last_entry_msc < (ulong)InpCooldownAfterMs)
      return(false);

   if(!InSession())
      return(false);
   if(InNewsBlackout())
      return(false);

   return(CheckRiskLimits());
  }

bool InSession()
  {
   int debut = ParseHhMm(InpSessionStart);
   int fin   = ParseHhMm(InpSessionEnd);
   int now   = MinutesOfDay(TimeCurrent());

   if(debut <= fin)
      return(now >= debut && now <= fin);
   return(now >= debut || now <= fin);   // a cheval sur minuit
  }

bool InNewsBlackout()
  {
   if(!InpAvoidNews)
      return(false);
   int total = ArraySize(g_news_minutes);
   if(total == 0)
      return(false);

   int now = MinutesOfDay(TimeCurrent());
   for(int i = 0; i < total; i++)
      if(MathAbs(now - g_news_minutes[i]) <= InpNewsBlackoutMin)
         return(true);
   return(false);
  }

int PositionsCountMine()
  {
   int compte = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(pos.SelectByIndex(i) && pos.Symbol() == g_symbol && pos.Magic() == InpMagic)
         compte++;
   return(compte);
  }

//+------------------------------------------------------------------+
//| Limites de risque                                                |
//+------------------------------------------------------------------+
void RollDay()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day != g_day)
     {
      g_day = dt.day;
      g_day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE);
     }
  }

bool CheckRiskLimits()
  {
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   //--- Drawdown global : arret definitif.
   if(g_start_equity > 0.0)
     {
      double dd = (g_start_equity - equity) / g_start_equity * 100.0;
      if(dd >= InpMaxDrawdownPct)
        {
         Halt(StringFormat("drawdown de %.2f%% depuis le lancement, plafond %.2f%%",
                           dd, InpMaxDrawdownPct));
         CloseAllMine("drawdown maximal");
         Alert("ScalpXAU : drawdown maximal atteint, EA arrete.");
         return(false);
        }
     }

   //--- Perte journaliere : arret jusqu'au lendemain.
   if(g_day_start_balance > 0.0)
     {
      double perte = (g_day_start_balance - balance) / g_day_start_balance * 100.0;
      if(perte >= InpMaxDailyLossPct)
        {
         if(!g_halted)
           {
            PrintFormat("Perte journaliere de %.2f%% atteinte : arret jusqu'a demain.",
                        perte);
            CloseAllMine("perte journaliere");
           }
         return(false);
        }
     }

   //--- Pertes consecutives : pause.
   if(g_consec_losses >= InpMaxConsecLosses)
     {
      g_cooldown_until = TimeCurrent() + InpCooldownMinutes * 60;
      g_consec_losses = 0;
      PrintFormat("%d pertes consecutives : pause de %d minutes.",
                  InpMaxConsecLosses, InpCooldownMinutes);
      return(false);
     }

   return(true);
  }

void Halt(const string raison)
  {
   if(g_halted)
      return;
   g_halted = true;
   g_halt_reason = raison;
   Print("ARRET DEFINITIF : ", raison);
  }

//+------------------------------------------------------------------+
//| Dimensionnement                                                  |
//+------------------------------------------------------------------+
double ComputeLots(const double stop_points)
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double risque  = balance * InpRiskPercent / 100.0;

   double risque_par_lot = stop_points * g_value_point;
   if(risque_par_lot <= 0.0)
      return(0.0);

   double lots = risque / risque_par_lot;

   //--- Arrondi AU PAS INFERIEUR : arrondir au plus proche depasserait le
   //--- risque annonce environ une fois sur deux.
   lots = MathFloor(lots / g_vol_step) * g_vol_step;
   lots = NormalizeDouble(lots, 2);

   if(lots < g_vol_min)
     {
      static datetime dernier_avis = 0;
      if(TimeCurrent() - dernier_avis > 300)
        {
         dernier_avis = TimeCurrent();
         PrintFormat("Volume calcule (%.4f) sous le lot minimal (%.2f) : trade refuse.",
                     risque / risque_par_lot, g_vol_min);
         PrintFormat("  Avec %.2f de risque et un stop de %.0f points, il faudrait "
                     "un stop d'au plus %.0f points pour tenir dans %.2f lot.",
                     risque, stop_points, risque / (g_vol_min * g_value_point),
                     g_vol_min);
        }
      return(0.0);
     }
   if(lots > g_vol_max)
      lots = g_vol_max;

   return(lots);
  }

//+------------------------------------------------------------------+
//| Ouverture                                                        |
//+------------------------------------------------------------------+
void OpenPosition(const int direction, const MqlTick &tick,
                  const double spread_pts, const string raison)
  {
   double stop_points = (double)InpStopLossPoints;
   double lots = ComputeLots(stop_points);
   if(lots <= 0.0)
      return;

   ENUM_ORDER_TYPE type = (direction > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double prix = (direction > 0) ? tick.ask : tick.bid;
   double sl, tp;

   if(direction > 0)
     {
      sl = prix - stop_points * g_point;
      tp = prix + stop_points * InpTakeProfitRatio * g_point;
     }
   else
     {
      sl = prix + stop_points * g_point;
      tp = prix - stop_points * InpTakeProfitRatio * g_point;
     }

   sl = NormalizeDouble(sl, g_digits);
   tp = NormalizeDouble(tp, g_digits);

   //--- Marge disponible.
   double marge = 0.0;
   if(!OrderCalcMargin(type, g_symbol, lots, prix, marge))
     {
      Print("OrderCalcMargin a echoue : ", GetLastError(), " — ordre annule.");
      return;
     }
   if(marge > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     {
      PrintFormat("Marge insuffisante : %.2f requis, %.2f libre.",
                  marge, AccountInfoDouble(ACCOUNT_MARGIN_FREE));
      return;
     }

   //--- Envoi avec reprise limitee.
   for(int essai = 1; essai <= InpMaxRetries; essai++)
     {
      if(trade.PositionOpen(g_symbol, type, lots, prix, sl, tp, "ScalpXAU"))
        {
         g_ticket      = trade.ResultOrder();
         // En netting comme en hedging, retrouver le ticket de position.
         if(!PositionSelectByTicket(g_ticket))
           {
            if(PositionSelect(g_symbol))
               g_ticket = PositionGetInteger(POSITION_TICKET);
           }
         g_open_time   = TimeCurrent();
         g_open_spread = spread_pts;
         g_open_dir    = direction;
         g_open_reason = raison;
         g_last_entry_msc = (ulong)tick.time_msc;

         if(InpVerboseLog)
            PrintFormat("ENTREE %s %.2f lot a %s | spread %.0f pts | %s",
                        (direction > 0 ? "ACHAT" : "VENTE"), lots,
                        DoubleToString(prix, g_digits), spread_pts, raison);
         return;
        }

      uint code = trade.ResultRetcode();
      PrintFormat("Echec d'ordre (essai %d/%d) : %u — %s",
                  essai, InpMaxRetries, code, trade.ResultRetcodeDescription());

      //--- Certaines erreurs ne se corrigent pas en reessayant.
      if(code == TRADE_RETCODE_NO_MONEY)
        {
         Halt("fonds insuffisants");
         return;
        }
      if(code == TRADE_RETCODE_MARKET_CLOSED || code == TRADE_RETCODE_TRADE_DISABLED)
         return;
      if(code == TRADE_RETCODE_INVALID_STOPS)
        {
         PrintFormat("  Stops refuses : le courtier impose au moins %d points.",
                     (int)g_stops_level);
         return;
        }

      //--- Requote ou contexte occupe : on rafraichit le prix et on retente.
      if(code == TRADE_RETCODE_REQUOTE || code == TRADE_RETCODE_PRICE_CHANGED ||
         code == TRADE_RETCODE_PRICE_OFF || code == TRADE_RETCODE_CONNECTION)
        {
         MqlTick frais;
         if(!SymbolInfoTick(g_symbol, frais))
            return;
         prix = (direction > 0) ? frais.ask : frais.bid;
         if(direction > 0)
           {
            sl = NormalizeDouble(prix - stop_points * g_point, g_digits);
            tp = NormalizeDouble(prix + stop_points * InpTakeProfitRatio * g_point, g_digits);
           }
         else
           {
            sl = NormalizeDouble(prix + stop_points * g_point, g_digits);
            tp = NormalizeDouble(prix - stop_points * InpTakeProfitRatio * g_point, g_digits);
           }
         continue;
        }

      return;   // erreur non geree : on n'insiste pas
     }
  }

//+------------------------------------------------------------------+
//| Gestion de la position ouverte                                   |
//+------------------------------------------------------------------+
void ManageOpenPosition(const MqlTick &tick, const double spread_pts)
  {
   if(!PositionSelectByTicket(g_ticket))
      return;   // DetectClosedPosition s'en occupera au tick suivant

   //--- Sortie sur le temps. C'est la sortie majoritaire de ce type d'EA :
   //--- l'impulsion n'a pas continue, on ne reste pas expose pour rien.
   int detenu = (int)(TimeCurrent() - g_open_time);
   if(detenu >= InpMaxHoldSeconds)
     {
      ClosePosition("temps ecoule");
      return;
     }

   //--- Sortie sur inversion : le flux qui a motive l'entree s'est retourne.
   if(InpExitOnReversal)
     {
      int    dir = 0;
      double imp = 0.0, eff = 0.0;
      if(ComputeSignal(spread_pts, dir, imp, eff) && dir == -g_open_dir)
        {
         ClosePosition("impulsion inversee");
         return;
        }
     }
  }

void ClosePosition(const string raison)
  {
   for(int essai = 1; essai <= InpMaxRetries; essai++)
     {
      if(trade.PositionClose(g_ticket, InpDeviationPoints))
        {
         if(InpVerboseLog)
            Print("SORTIE demandee : ", raison);
         return;
        }
      uint code = trade.ResultRetcode();
      PrintFormat("Echec de cloture (essai %d/%d) : %u — %s",
                  essai, InpMaxRetries, code, trade.ResultRetcodeDescription());
      if(code == TRADE_RETCODE_MARKET_CLOSED)
         return;
     }
   Print("ATTENTION : cloture impossible apres ", InpMaxRetries,
         " tentatives. Position laissee ouverte, surveille-la manuellement.");
  }

void CloseAllMine(const string raison)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(!pos.SelectByIndex(i))
         continue;
      if(pos.Symbol() != g_symbol || pos.Magic() != InpMagic)
         continue;
      trade.PositionClose(pos.Ticket(), InpDeviationPoints);
     }
   Print("Toutes les positions fermees : ", raison);
  }

//+------------------------------------------------------------------+
//| Detection de cloture et journalisation                           |
//+------------------------------------------------------------------+
void DetectClosedPosition()
  {
   if(g_ticket == 0)
      return;
   if(PositionSelectByTicket(g_ticket))
      return;   // toujours ouverte

   //--- La position a disparu : retrouver l'operation de sortie.
   ulong ticket = g_ticket;
   g_ticket = 0;

   if(!HistorySelect(g_open_time - 60, TimeCurrent() + 60))
     {
      Print("Historique indisponible : trade #", ticket, " non journalise.");
      return;
     }

   double resultat = 0.0;
   double prix_sortie = 0.0;
   double prix_entree = 0.0;
   datetime fermeture = TimeCurrent();
   bool trouve = false;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID) != ticket)
         continue;

      long entree = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entree == DEAL_ENTRY_IN)
         prix_entree = HistoryDealGetDouble(deal, DEAL_PRICE);
      else
        {
         resultat += HistoryDealGetDouble(deal, DEAL_PROFIT)
                     + HistoryDealGetDouble(deal, DEAL_SWAP)
                     + HistoryDealGetDouble(deal, DEAL_COMMISSION);
         prix_sortie = HistoryDealGetDouble(deal, DEAL_PRICE);
         fermeture   = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
         trouve = true;
        }
     }

   if(!trouve)
      return;

   int    duree  = (int)(fermeture - g_open_time);
   double points = 0.0;
   if(prix_entree > 0.0 && prix_sortie > 0.0)
      points = (prix_sortie - prix_entree) / g_point * (g_open_dir > 0 ? 1.0 : -1.0);

   //--- Statistiques
   g_trades++;
   g_sum_hold += duree;
   if(resultat > 0.0)
     {
      g_wins++;
      g_gross_profit += resultat;
      g_consec_losses = 0;
     }
   else
     {
      g_gross_loss += MathAbs(resultat);
      if(resultat < 0.0)
         g_consec_losses++;
     }

   if(InpVerboseLog)
      PrintFormat("TRADE #%I64u %s | entree : %s | spread a l'entree %.0f pts | "
                  "duree %d s | %+.0f points | %+.2f %s",
                  ticket, (g_open_dir > 0 ? "ACHAT" : "VENTE"), g_open_reason,
                  g_open_spread, duree, points, resultat,
                  AccountInfoString(ACCOUNT_CURRENCY));

   //--- Un point d'etape regulier evite d'avoir a lire tout le journal.
   if(g_trades % 20 == 0)
      PrintSessionStats();
  }
//+------------------------------------------------------------------+
