// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title RPSArena — commit-reveal rock-paper-scissors with stakes
/// @notice Flow:
///   1. Creator calls createMatch(commitment) with the stake as msg.value.
///      commitment = keccak256(abi.encodePacked(address(this), creator, uint8(move), salt))
///   2. Joiner calls joinMatch(id, move) with the same stake. The joiner's move is public,
///      which is safe because the creator's move is already locked in by the commitment.
///   3. Creator calls reveal(id, move, salt) before the deadline. The winner is paid.
///   4. If the creator never reveals, the joiner calls claimTimeout(id) and wins the pot.
///      (A creator who sees they lost and refuses to reveal simply forfeits.)
///   Open matches nobody has joined can be cancelled by the creator for a refund.
///
///   Session keys (play without a wallet popup per move):
///   - A player calls startSession(key, duration, allowance, gasFund) once, depositing MON into
///     their in-contract balance (`credits`) and forwarding `gasFund` to the key for gas.
///   - The key can then create/join matches *on the player's behalf* using that balance
///     (createMatchFromBalance / joinMatchFromBalance), and reveal / claim / cancel them.
///     The player, not the key, is recorded as creator/joiner, so stats stay on their wallet.
///   - The key can never withdraw, can stake at most `allowance` in total, and only until expiry.
///     Stakes paid from the balance are paid back into the balance, so play continues popup-free.
contract RPSArena {
    enum Move { None, Rock, Paper, Scissors }
    enum Status { None, Open, Joined, Settled, Cancelled }

    struct Match {
        address creator;
        address joiner;
        uint256 stake;
        bytes32 commitment;
        Move creatorMove;
        Move joinerMove;
        Status status;
        uint64 revealDeadline;
    }

    uint64 public immutable revealWindow;
    uint256 public nextMatchId = 1;
    mapping(uint256 => Match) public matches;
    /// @notice In-contract balance: deposits, winnings from balance-funded matches, and payouts
    ///         that could not be pushed (e.g. receiver reverted). Pulled with withdraw().
    mapping(address => uint256) public credits;

    struct Session {
        address key;
        uint64 expiry;
        uint256 allowance; // total stake the key may still spend from the player's balance
    }

    /// @notice player => their current session (at most one).
    mapping(address => Session) public sessions;
    /// @notice session key => the player it acts for.
    mapping(address => address) public sessionOwner;
    /// @notice Bit 1: creator's stake came from their balance; bit 2: joiner's did.
    ///         Those stakes are paid back into `credits` instead of being pushed to the wallet.
    mapping(uint256 => uint8) public fundedFromBalance;

    bool private locked;

    event MatchCreated(uint256 indexed id, address indexed creator, uint256 stake);
    event MatchJoined(uint256 indexed id, address indexed joiner, Move joinerMove, uint64 revealDeadline);
    event MatchSettled(
        uint256 indexed id,
        address indexed winner, // address(0) for a draw
        Move creatorMove,
        Move joinerMove,
        bool forfeit
    );
    event MatchCancelled(uint256 indexed id);
    event CreditAdded(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);
    event Deposited(address indexed account, uint256 amount);
    event SessionStarted(address indexed player, address indexed key, uint64 expiry, uint256 allowance);
    event SessionEnded(address indexed player, address indexed key);

    error InvalidMove();
    error WrongStatus();
    error WrongStake();
    error NotCreator();
    error NotJoiner();
    error CannotPlaySelf();
    error BadReveal();
    error DeadlineNotPassed();
    error DeadlinePassed();
    error NothingToWithdraw();
    error Reentrancy();
    error SessionInvalid();
    error InsufficientBalance();
    error AllowanceExceeded();

    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(uint64 _revealWindow) {
        revealWindow = _revealWindow == 0 ? 10 minutes : _revealWindow;
    }

    /// @notice Helper so clients can build the exact commitment the contract expects.
    function makeCommitment(address player, Move move, bytes32 salt) public view returns (bytes32) {
        return keccak256(abi.encodePacked(address(this), player, uint8(move), salt));
    }

    // ---------------------------------------------------------------- matches

    function createMatch(bytes32 commitment) external payable returns (uint256 id) {
        id = _create(msg.sender, commitment, msg.value);
    }

    /// @notice Create a match paying the stake from the player's in-contract balance.
    ///         Callable by the player directly or by their live session key.
    function createMatchFromBalance(bytes32 commitment, uint256 stake) external returns (uint256 id) {
        (address player, bool viaSession) = _actor(true);
        _spend(player, viaSession, stake);
        id = _create(player, commitment, stake);
        fundedFromBalance[id] |= 1;
    }

    function joinMatch(uint256 id, Move move) external payable {
        if (msg.value != matches[id].stake) revert WrongStake();
        _join(id, msg.sender, move);
    }

    /// @notice Join a match paying the stake from the player's in-contract balance.
    function joinMatchFromBalance(uint256 id, Move move) external {
        (address player, bool viaSession) = _actor(true);
        _join(id, player, move);
        _spend(player, viaSession, matches[id].stake);
        fundedFromBalance[id] |= 2;
    }

    function reveal(uint256 id, Move move, bytes32 salt) external nonReentrant {
        (address player, ) = _actor(false);
        Match storage m = matches[id];
        if (m.status != Status.Joined) revert WrongStatus();
        if (player != m.creator) revert NotCreator();
        if (block.timestamp > m.revealDeadline) revert DeadlinePassed();
        if (move == Move.None) revert InvalidMove();
        if (makeCommitment(player, move, salt) != m.commitment) revert BadReveal();

        m.creatorMove = move;
        m.status = Status.Settled;

        uint256 pot = m.stake * 2;
        int8 result = _outcome(move, m.joinerMove); // 1 creator wins, -1 joiner wins, 0 draw
        address winner;
        if (result == 1) {
            winner = m.creator;
            _payout(id, m.creator, true, pot);
        } else if (result == -1) {
            winner = m.joiner;
            _payout(id, m.joiner, false, pot);
        } else {
            _payout(id, m.creator, true, m.stake);
            _payout(id, m.joiner, false, m.stake);
        }
        emit MatchSettled(id, winner, move, m.joinerMove, false);
    }

    function claimTimeout(uint256 id) external nonReentrant {
        (address player, ) = _actor(false);
        Match storage m = matches[id];
        if (m.status != Status.Joined) revert WrongStatus();
        if (player != m.joiner) revert NotJoiner();
        if (block.timestamp <= m.revealDeadline) revert DeadlineNotPassed();

        m.status = Status.Settled;
        _payout(id, m.joiner, false, m.stake * 2);
        emit MatchSettled(id, m.joiner, Move.None, m.joinerMove, true);
    }

    function cancelMatch(uint256 id) external nonReentrant {
        (address player, ) = _actor(false);
        Match storage m = matches[id];
        if (m.status != Status.Open) revert WrongStatus();
        if (player != m.creator) revert NotCreator();
        m.status = Status.Cancelled;
        _payout(id, m.creator, true, m.stake);
        emit MatchCancelled(id);
    }

    // ---------------------------------------------------------------- balance & sessions

    function deposit() external payable {
        credits[msg.sender] += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    /// @notice Withdraw your whole in-contract balance. Session keys can never call this for you.
    function withdraw() external nonReentrant {
        uint256 amount = credits[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        credits[msg.sender] = 0;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "withdraw failed");
        emit Withdrawn(msg.sender, amount);
    }

    /// @notice Authorize `key` to play for you for `duration` seconds, staking at most `allowance`
    ///         in total from your balance. msg.value - gasFund is deposited; gasFund goes to the key
    ///         so it can pay for its own transactions. Replaces any previous session.
    function startSession(address key, uint64 duration, uint256 allowance, uint256 gasFund)
        external
        payable
        nonReentrant
    {
        if (key == address(0) || key == msg.sender || duration == 0) revert SessionInvalid();
        if (sessionOwner[msg.sender] != address(0)) revert SessionInvalid(); // a key can't open sessions
        address owner = sessionOwner[key];
        if (owner != address(0) && owner != msg.sender) revert SessionInvalid();
        if (gasFund > msg.value) revert WrongStake();

        _endSession(msg.sender);
        uint64 expiry = uint64(block.timestamp) + duration;
        sessions[msg.sender] = Session(key, expiry, allowance);
        sessionOwner[key] = msg.sender;
        emit SessionStarted(msg.sender, key, expiry, allowance);

        uint256 dep = msg.value - gasFund;
        if (dep > 0) {
            credits[msg.sender] += dep;
            emit Deposited(msg.sender, dep);
        }
        if (gasFund > 0) {
            (bool ok, ) = key.call{value: gasFund}("");
            require(ok, "gas fund failed");
        }
    }

    /// @notice End the session. Callable by the player or by the session key itself.
    function endSession() external {
        address owner = sessionOwner[msg.sender];
        address player = owner != address(0) ? owner : msg.sender;
        if (sessions[player].key == address(0)) revert SessionInvalid();
        _endSession(player);
    }

    function getMatch(uint256 id) external view returns (Match memory) {
        return matches[id];
    }

    // ---------------------------------------------------------------- internals

    /// @dev Who is acting: the player a session key stands for, or msg.sender itself.
    ///      Spending needs a live (unexpired) session; settling (reveal/claim/cancel) only moves
    ///      money back to the player, so an expired-but-not-ended key may still do it.
    function _actor(bool requireLive) private view returns (address player, bool viaSession) {
        address owner = sessionOwner[msg.sender];
        if (owner == address(0)) return (msg.sender, false);
        Session storage s = sessions[owner];
        if (s.key != msg.sender) revert SessionInvalid();
        if (requireLive && block.timestamp > s.expiry) revert SessionInvalid();
        return (owner, true);
    }

    function _spend(address player, bool viaSession, uint256 amount) private {
        if (credits[player] < amount) revert InsufficientBalance();
        if (viaSession) {
            Session storage s = sessions[player];
            if (amount > s.allowance) revert AllowanceExceeded();
            s.allowance -= amount;
        }
        credits[player] -= amount;
    }

    function _endSession(address player) private {
        address key = sessions[player].key;
        if (key == address(0)) return;
        delete sessionOwner[key];
        delete sessions[player];
        emit SessionEnded(player, key);
    }

    function _create(address creator, bytes32 commitment, uint256 stake) private returns (uint256 id) {
        if (commitment == bytes32(0)) revert BadReveal();
        id = nextMatchId++;
        Match storage m = matches[id];
        m.creator = creator;
        m.stake = stake;
        m.commitment = commitment;
        m.status = Status.Open;
        emit MatchCreated(id, creator, stake);
    }

    function _join(uint256 id, address joiner, Move move) private {
        Match storage m = matches[id];
        if (m.status != Status.Open) revert WrongStatus();
        if (move == Move.None || uint8(move) > 3) revert InvalidMove();
        if (joiner == m.creator) revert CannotPlaySelf();

        m.joiner = joiner;
        m.joinerMove = move;
        m.status = Status.Joined;
        m.revealDeadline = uint64(block.timestamp) + revealWindow;
        emit MatchJoined(id, joiner, move, m.revealDeadline);
    }

    /// @dev Money from a balance-funded stake goes back to the balance; otherwise it is pushed.
    function _payout(uint256 id, address to, bool isCreator, uint256 amount) private {
        if (amount == 0) return;
        if (fundedFromBalance[id] & (isCreator ? 1 : 2) != 0) {
            credits[to] += amount;
            emit CreditAdded(to, amount);
        } else {
            _pay(to, amount);
        }
    }

    /// @dev Push payment with a gas cap; if the receiver reverts, fall back to a pull credit
    ///      so a hostile contract can never block settlement.
    function _pay(address to, uint256 amount) private {
        if (amount == 0) return;
        (bool ok, ) = to.call{value: amount, gas: 30_000}("");
        if (!ok) {
            credits[to] += amount;
            emit CreditAdded(to, amount);
        }
    }

    function _outcome(Move a, Move b) private pure returns (int8) {
        if (a == b) return 0;
        if (
            (a == Move.Rock && b == Move.Scissors) ||
            (a == Move.Paper && b == Move.Rock) ||
            (a == Move.Scissors && b == Move.Paper)
        ) return 1;
        return -1;
    }
}
